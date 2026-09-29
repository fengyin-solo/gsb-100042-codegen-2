"""发运单管理业务规则：状态流转、字段校验、批量导入的暂存核对与并发落库都收在这里。

批量导入约定：
1. 文件先暂存（stage），逐行核对运单编号、发货方、收货方、发运批次，核对时不落库。
2. 核对通过后提交（commit）落库；落库全程加锁，同一运单编号只允许一条记录落库。
3. 已存在的运单编号：发货方/收货方/发运批次一致 → 视为重复行跳过；不一致 → 视为冲突行保留原记录。
4. 落库结果驱动批次汇总、发运单列表与明细同时重算（三处都从同一仓库读取最新数据）。
"""
from __future__ import annotations

import csv
import io
import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "shipment"
REQUIRED_FIELDS = ["运单编号", "发货方", "收货方"]
IMPORT_FIELDS = ["运单编号", "发货方", "收货方", "发运批次"]
IMPORT_COLUMNS = ["运单编号", "发货方", "收货方", "发运批次", "货物名称", "温层要求", "发运日期", "预计到达"]
STATUS_ORDER = ["待发运", "在途", "已到达", "已签收", "已退回"]
ACTION_RULES = {"确认发运": "在途", "确认到达": "已到达", "退回货物": "已退回"}
NEGATIVE_ACTIONS = []

# 导入落库锁：并发提交时串行化，保证同一运单编号只落库一次。
_import_lock = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_shipment_id() -> int:
    rows = store.rows(MODULE)
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _find_shipment_by_no(waybill_no: str) -> dict[str, Any] | None:
    target = waybill_no.strip()
    for row in store.rows(MODULE):
        if str(row.get("运单编号", "")).strip() == target:
            return row
    return None


class ShipmentService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("运单编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"发运单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于发运单管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"发运单已{action}"

    # ------------------------------------------------------------------
    # 批量导入：暂存 + 逐行核对
    # ------------------------------------------------------------------
    def stage_import(self, filename: str, content: bytes) -> dict[str, Any]:
        """暂存上传的文件并逐行核对，返回批次记录（此时不落库）。"""
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        parsed: list[dict[str, Any]] = []
        for line_no, raw in enumerate(reader, start=2):
            row = {col: str(raw.get(col) or "").strip() for col in IMPORT_COLUMNS}
            missing = [field for field in IMPORT_FIELDS if not row[field]]
            if missing:
                row["核对结果"] = "无效"
                row["原因"] = f"{missing[0]}为空"
            else:
                row["核对结果"] = "有效"
                row["原因"] = None
            row["行号"] = line_no
            row["落库结果"] = None
            parsed.append(row)

        seq = store.next_batch_seq()
        batch = {
            "id": seq,
            "批次编号": f"IMPORT-{datetime.now().strftime('%Y%m%d')}-{seq:03d}",
            "文件名称": filename,
            "导入时间": _now(),
            "状态": "已暂存",
            "总行数": len(parsed),
            "有效行数": sum(1 for row in parsed if row["核对结果"] == "有效"),
            "无效行数": sum(1 for row in parsed if row["核对结果"] == "无效"),
            "新增行数": 0,
            "冲突行数": 0,
            "重复行数": 0,
            "落库时间": None,
            "rows": parsed,
        }
        store.add_batch(batch)
        return batch

    def commit_import(self, batch_id: int) -> tuple[dict[str, Any] | None, str]:
        """提交暂存批次并落库。

        加锁后逐行处理：运单编号不存在 → 新增；已存在且关键信息一致 → 重复（跳过）；
        已存在但关键信息不一致 → 冲突（保留原记录）。重复提交不产生新运单。
        """
        batch = store.find_batch(batch_id)
        if batch is None:
            return None, f"导入批次 {batch_id} 不存在或已清理"
        if batch["状态"] == "已落库":
            return batch, "该批次已落库，无需重复提交"

        with _import_lock:
            # 锁内二次确认，拦住并发下的重复提交。
            if batch["状态"] == "已落库":
                return batch, "该批次已落库，无需重复提交"

            for row in batch["rows"]:
                if row["核对结果"] != "有效":
                    row["落库结果"] = "无效"
                    continue
                waybill_no = row["运单编号"]
                existing = _find_shipment_by_no(waybill_no)
                if existing is None:
                    entry = {
                        "id": _next_shipment_id(),
                        "运单编号": waybill_no,
                        "发货方": row["发货方"],
                        "收货方": row["收货方"],
                        "发运批次": row["发运批次"],
                        "货物名称": row["货物名称"],
                        "温层要求": row["温层要求"],
                        "发运日期": row["发运日期"],
                        "预计到达": row["预计到达"],
                        "运单状态": "待发运",
                        "status": STATUS_ORDER[0],
                        "pending": True,
                        "abnormal": False,
                    }
                    store.rows(MODULE).append(entry)
                    row["落库结果"] = "新增"
                elif (
                    str(existing.get("发货方", "")).strip() == row["发货方"]
                    and str(existing.get("收货方", "")).strip() == row["收货方"]
                    and str(existing.get("发运批次", "")).strip() == row["发运批次"]
                ):
                    row["落库结果"] = "重复"
                else:
                    row["落库结果"] = "冲突"

            batch["新增行数"] = sum(1 for row in batch["rows"] if row["落库结果"] == "新增")
            batch["冲突行数"] = sum(1 for row in batch["rows"] if row["落库结果"] == "冲突")
            batch["重复行数"] = sum(1 for row in batch["rows"] if row["落库结果"] == "重复")
            batch["状态"] = "已落库"
            batch["落库时间"] = _now()

        return batch, "导入完成，批次汇总、发运单列表与明细已重算"

    def list_batches(self) -> list[dict[str, Any]]:
        return store.list_batches()

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        return store.find_batch(batch_id)

    def batch_summary(self) -> list[dict[str, Any]]:
        """批次汇总：按发运批次分组，统计运单数量与状态分布。"""
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in store.rows(MODULE):
            key = str(row.get("发运批次") or "未分组")
            groups.setdefault(key, []).append(row)
        summary: list[dict[str, Any]] = []
        for batch_no in sorted(groups):
            items = groups[batch_no]
            summary.append({
                "发运批次": batch_no,
                "运单总数": len(items),
                "待发运": sum(1 for r in items if r.get("status") == "待发运"),
                "在途": sum(1 for r in items if r.get("status") == "在途"),
                "已到达": sum(1 for r in items if r.get("status") == "已到达"),
                "已签收": sum(1 for r in items if r.get("status") == "已签收"),
                "已退回": sum(1 for r in items if r.get("status") == "已退回"),
                "异常数": sum(1 for r in items if r.get("abnormal")),
            })
        return summary
