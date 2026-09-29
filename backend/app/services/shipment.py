"""发运单管理业务规则：状态流转、字段校验、批量暂存与导入。"""
from __future__ import annotations

import csv
import io
import os
import tempfile
import threading
import uuid
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "shipment"
WAYBILL_FIELD = "运单编号"
SENDER_FIELD = "发货方"
RECEIVER_FIELD = "收货方"
BATCH_FIELD = "发运批次"
REQUIRED_FIELDS = [WAYBILL_FIELD, SENDER_FIELD, RECEIVER_FIELD]
IMPORT_REQUIRED_FIELDS = REQUIRED_FIELDS + [BATCH_FIELD]
LIST_FIELDS = [
    WAYBILL_FIELD,
    SENDER_FIELD,
    RECEIVER_FIELD,
    BATCH_FIELD,
    "货物名称",
    "温层要求",
    "发运日期",
    "预计到达",
    "运单状态",
]
STATUS_ORDER = ["待发运", "在途", "已到达", "已签收", "已退回"]
ACTION_RULES = {"确认发运": "在途", "确认到达": "已到达", "退回货物": "已退回"}
NEGATIVE_ACTIONS = []
MAX_IMPORT_BYTES = 5 * 1024 * 1024
STAGED_PREFIX = "shipment-import-"


class ShipmentService:
    """所有导入任务也放在服务实例中；当前版本对应单进程内存仓库。"""

    def __init__(self) -> None:
        self._imports_lock = threading.RLock()
        self._imports: dict[str, dict[str, Any]] = {}

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        batch: str | None = None,
        sender: str | None = None,
        receiver: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.snapshot(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get(WAYBILL_FIELD, ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if batch:
            rows = [row for row in rows if batch in str(row.get(BATCH_FIELD, ""))]
        if sender:
            rows = [row for row in rows if sender in str(row.get(SENDER_FIELD, ""))]
        if receiver:
            rows = [row for row in rows if receiver in str(row.get(RECEIVER_FIELD, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing

        with store.lock:
            rows = store.rows(MODULE)
            waybill_no = str(values[WAYBILL_FIELD]).strip()
            existing = self._find_row_by_waybill(rows, waybill_no)
            if existing is not None:
                entry = dict(existing)
                return entry, []

            entry = self._build_entry(self._next_id(rows), self._clean_values(values))
            rows.append(entry)
            return dict(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"发运单 {entry_id} 不存在或已归档"
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于发运单管理可执行范围"
            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                return None, f"目标状态「{target}」不在允许的状态序列里"
            live_entry = self._find_row_by_id(store.rows(MODULE), entry_id)
            if live_entry is None:
                return None, f"发运单 {entry_id} 不存在或已归档"
            live_entry["status"] = target
            live_entry["运单状态"] = target
            live_entry["pending"] = target != STATUS_ORDER[-1]
            live_entry["abnormal"] = action in NEGATIVE_ACTIONS
            return dict(live_entry), f"发运单已{action}"

    def stats(self) -> list[dict[str, object]]:
        rows = store.snapshot(MODULE)
        pending = sum(1 for row in rows if row.get("status") == "待发运")
        in_transit = sum(1 for row in rows if row.get("status") == "在途")
        today = datetime.now().date().isoformat()
        arrived_today = sum(
            1 for row in rows
            if row.get("status") == "已到达" and str(row.get("发运日期", "")).startswith(today)
        )
        return [
            {"label": "发运单总数", "value": len(rows)},
            {"label": "待发运单", "value": pending},
            {"label": "在途运单", "value": in_transit},
            {"label": "今日到达", "value": arrived_today},
        ]

    def batch_summary(self) -> list[dict[str, object]]:
        """发运批次汇总由当前发运单列表即时分组计算，不保存陈旧统计。"""
        rows = store.snapshot(MODULE)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            batch = str(row.get(BATCH_FIELD) or "未分批")
            grouped.setdefault(batch, []).append(row)

        summary = []
        for batch, batch_rows in grouped.items():
            summary.append({
                BATCH_FIELD: batch,
                "运单数": len(batch_rows),
                "待发运": sum(1 for row in batch_rows if row.get("status") == "待发运"),
                "在途": sum(1 for row in batch_rows if row.get("status") == "在途"),
                "已到达": sum(1 for row in batch_rows if row.get("status") == "已到达"),
                "已签收": sum(1 for row in batch_rows if row.get("status") == "已签收"),
                "已退回": sum(1 for row in batch_rows if row.get("status") == "已退回"),
            })
        return sorted(summary, key=lambda item: str(item[BATCH_FIELD]))

    def stage_import(self, filename: str, content: bytes) -> dict[str, Any]:
        """先把原文件落临时目录，再逐行核对四个关键口径。"""
        if len(content) > MAX_IMPORT_BYTES:
            raise ValueError("导入文件不能超过 5MB")
        if not filename.lower().endswith(".csv"):
            raise ValueError("仅支持 CSV 格式的批量导入文件")

        job_id = uuid.uuid4().hex
        staged_file = self._write_staged_file(job_id, filename, content)
        try:
            parsed_rows, headers = self._parse_csv(content)
        except ValueError as exc:
            return self._save_error_job(job_id, filename, staged_file, str(exc))

        missing_headers = [field for field in IMPORT_REQUIRED_FIELDS if field not in headers]
        if missing_headers:
            return self._save_error_job(
                job_id,
                filename,
                staged_file,
                f"缺少必要列：{'、'.join(missing_headers)}",
            )

        row_results: list[dict[str, Any]] = []
        seen: dict[str, int] = {}
        existing_rows = store.snapshot(MODULE)
        existing = {
            str(row.get(WAYBILL_FIELD) or "").strip(): row
            for row in existing_rows
            if str(row.get(WAYBILL_FIELD) or "").strip()
        }

        for index, raw in enumerate(parsed_rows, start=2):
            values = self._clean_values(raw)
            waybill_no = values.get(WAYBILL_FIELD, "")
            sender = values.get(SENDER_FIELD, "")
            receiver = values.get(RECEIVER_FIELD, "")
            batch = values.get(BATCH_FIELD, "")
            base = {
                "row_number": index,
                "waybill_no": waybill_no or None,
                "sender": sender or None,
                "receiver": receiver or None,
                "shipment_batch": batch or None,
                "original": values,
                "entry_id": None,
            }
            missing = [
                label for field, label in (
                    (WAYBILL_FIELD, "运单编号"),
                    (SENDER_FIELD, "发货方"),
                    (RECEIVER_FIELD, "收货方"),
                    (BATCH_FIELD, "发运批次"),
                ) if not values.get(field)
            ]
            if missing:
                row_results.append({
                    **base,
                    "status": "invalid",
                    "message": f"缺少必填字段：{'、'.join(missing)}",
                })
                continue
            if waybill_no in seen:
                row_results.append({
                    **base,
                    "status": "invalid",
                    "message": f"文件内运单编号重复，首次出现在第 {seen[waybill_no]} 行",
                })
                continue
            if waybill_no in existing:
                row_results.append({
                    **base,
                    "status": "conflict",
                    "entry_id": existing[waybill_no].get("id"),
                    "message": "运单编号已存在，冲突行保留原记录",
                })
                continue
            seen[waybill_no] = index
            row_results.append({**base, "status": "valid", "message": "暂存核对通过"})

        valid_count = self._count(row_results, "valid")
        conflict_count = self._count(row_results, "conflict")
        invalid_count = self._count(row_results, "invalid")
        if valid_count:
            status = "staged"
            message = f"文件已暂存，{valid_count} 行可导入，{conflict_count} 行冲突，{invalid_count} 行无效"
        elif conflict_count:
            status = "conflict_only"
            message = f"全部 {conflict_count} 行与已有运单冲突，原记录已保留"
        else:
            status = "invalid"
            message = f"没有可导入行，{invalid_count} 行未通过核对"

        job = {
            "job_id": job_id,
            "filename": filename,
            "staged_file": staged_file,
            "status": status,
            "message": message,
            "total": len(row_results),
            "valid": valid_count,
            "conflicts": conflict_count,
            "invalid": invalid_count,
            "imported": 0,
            "rows": row_results,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._imports_lock:
            self._imports[job_id] = job
        return self._public_job(job)

    def get_import(self, job_id: str) -> dict[str, Any] | None:
        with self._imports_lock:
            job = self._imports.get(job_id)
            return self._public_job(job) if job else None

    def commit_import(self, job_id: str) -> tuple[dict[str, Any] | None, str]:
        """确认导入：同一运单在全局锁内二次查重，只有一个并发批次能落库。"""
        with self._imports_lock:
            job = self._imports.get(job_id)
            if job is None:
                return None, "导入任务不存在或暂存文件已过期"
            if job["status"] == "imported":
                return self._public_job(job), "该导入任务已确认，未重复生成运单"
            if job["valid"] == 0:
                return self._public_job(job), "暂存文件中没有可导入的有效行"

        inserted_entries: list[dict[str, Any]] = []
        with store.lock:
            rows = store.rows(MODULE)
            with self._imports_lock:
                live_job = self._imports[job_id]
                for row_result in live_job["rows"]:
                    if row_result["status"] != "valid":
                        continue
                    waybill_no = row_result["waybill_no"]
                    existing = self._find_row_by_waybill(rows, waybill_no)
                    if existing is not None:
                        row_result["status"] = "conflict"
                        row_result["entry_id"] = existing.get("id")
                        row_result["message"] = "并发导入时运单已被其他批次落库，保留原记录"
                        continue

                    entry = self._build_entry(self._next_id(rows), row_result["original"])
                    rows.append(entry)
                    inserted_entries.append(dict(entry))
                    row_result["status"] = "imported"
                    row_result["entry_id"] = entry["id"]
                    row_result["message"] = "导入成功"

                live_job["imported"] = len(inserted_entries)
                live_job["valid"] = self._count(live_job["rows"], "valid")
                live_job["conflicts"] = self._count(live_job["rows"], "conflict")
                live_job["invalid"] = self._count(live_job["rows"], "invalid")
                live_job["status"] = "imported"
                live_job["message"] = (
                    f"导入完成：新增 {live_job['imported']} 单，"
                    f"冲突 {live_job['conflicts']} 行，无效 {live_job['invalid']} 行"
                )
                result = self._public_job(
                    live_job,
                    batch_summary=self.batch_summary(),
                    entries=inserted_entries,
                )
        return result, "导入完成，批次汇总、列表与明细已同步重算"

    def _save_error_job(
        self,
        job_id: str,
        filename: str,
        staged_file: str,
        message: str,
    ) -> dict[str, Any]:
        job = {
            "job_id": job_id,
            "filename": filename,
            "staged_file": staged_file,
            "status": "invalid",
            "message": message,
            "total": 0,
            "valid": 0,
            "conflicts": 0,
            "invalid": 0,
            "imported": 0,
            "rows": [],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._imports_lock:
            self._imports[job_id] = job
        return self._public_job(job)

    def _parse_csv(self, content: bytes) -> tuple[list[dict[str, str]], list[str]]:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV 文件需使用 UTF-8 编码") from exc
        reader = csv.DictReader(io.StringIO(text, newline=""))
        headers = [str(name or "").strip() for name in (reader.fieldnames or [])]
        if not headers:
            raise ValueError("CSV 文件缺少表头")
        parsed = []
        for raw in reader:
            parsed.append({str(key or "").strip(): str(value or "").strip() for key, value in raw.items()})
        return parsed, headers

    def _write_staged_file(self, job_id: str, filename: str, content: bytes) -> str:
        safe_name = f"{job_id}-{os.path.basename(filename).replace(os.sep, '_')}"
        fd, path = tempfile.mkstemp(prefix=STAGED_PREFIX, suffix=f"-{safe_name}")
        try:
            with os.fdopen(fd, "wb") as staged:
                staged.write(content)
        except Exception:
            self._safe_unlink(path)
            raise
        return path

    def _clean_values(self, values: dict[str, Any]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, value in values.items():
            name = str(key or "").strip()
            if name:
                cleaned[name] = str(value or "").strip()
        return cleaned

    def _build_entry(self, entry_id: int, values: dict[str, str]) -> dict[str, Any]:
        business_status = values.get("运单状态") or values.get("status") or STATUS_ORDER[0]
        if business_status not in STATUS_ORDER:
            business_status = STATUS_ORDER[0]
        entry: dict[str, Any] = {"id": entry_id}
        for field in LIST_FIELDS:
            entry[field] = values.get(field, "")
        entry["运单状态"] = values.get("运单状态") or business_status
        entry["status"] = business_status
        entry["pending"] = business_status != STATUS_ORDER[-1]
        entry["abnormal"] = False
        return entry

    def _next_id(self, rows: list[dict[str, Any]]) -> int:
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    def _find_row_by_waybill(
        self, rows: list[dict[str, Any]], waybill_no: str
    ) -> dict[str, Any] | None:
        for row in rows:
            if str(row.get(WAYBILL_FIELD) or "").strip() == waybill_no:
                return row
        return None

    def _find_row_by_id(
        self, rows: list[dict[str, Any]], entry_id: int
    ) -> dict[str, Any] | None:
        for row in rows:
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def _count(self, rows: list[dict[str, Any]], status: str) -> int:
        return sum(1 for row in rows if row["status"] == status)

    def _public_job(
        self,
        job: dict[str, Any],
        *,
        batch_summary: list[dict[str, Any]] | None = None,
        entries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "job_id": job["job_id"],
            "filename": job["filename"],
            "staged_file": job["staged_file"],
            "status": job["status"],
            "message": job["message"],
            "total": job["total"],
            "valid": job["valid"],
            "conflicts": job["conflicts"],
            "invalid": job["invalid"],
            "imported": job.get("imported", 0),
            "rows": [dict(row) for row in job["rows"]],
            "batch_summary": batch_summary if batch_summary is not None else [],
            "entries": entries if entries is not None else [],
        }

    def _safe_unlink(self, path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
