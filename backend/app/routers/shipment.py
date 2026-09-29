"""发运单管理接口：维护发运单，覆盖批量导入、批次汇总与确认发运等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.shipment import ShipmentService

router = APIRouter(prefix="/api/shipment", tags=["发运单管理"])

service = ShipmentService()

LIST_FIELDS = ["运单编号", "发货方", "收货方", "发运批次", "货物名称", "温层要求", "发运日期", "预计到达", "运单状态"]
STATUSES = ["待发运", "在途", "已到达", "已签收", "已退回"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按运单编号检索"),
    status: str | None = Query(default=None, description="待发运、在途、已到达、已签收、已退回"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按运单编号与状态过滤发运单管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/batch-summary")
def batch_summary() -> dict[str, Any]:
    """批次汇总：按发运批次分组统计运单数量与状态分布，导入后随列表、明细一起重算。"""
    return {"items": service.batch_summary(), "total": len(service.batch_summary())}


@router.get("/batches")
def list_batches() -> dict[str, Any]:
    """列出全部导入批次（含暂存与已落库），用于批次汇总与导入历史。"""
    batches = service.list_batches()
    return {"items": batches, "total": len(batches)}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int) -> dict[str, Any]:
    """读取单个导入批次明细，含逐行核对结果与落库结果。"""
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"导入批次 {batch_id} 不存在或已清理")
    return batch


@router.post("/import/stage")
async def stage_import(file: UploadFile = File(..., description="CSV 发运单文件")) -> dict[str, Any]:
    """暂存上传的发运单文件并逐行核对运单编号、发货方、收货方、发运批次。

    此接口只暂存、核对，不落库；核对结果逐行返回，供前端预览后再提交落库。
    """
    filename = file.filename or "未命名.csv"
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="仅支持 CSV 文件，请检查文件格式")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空，请填写数据后再导入")
    batch = service.stage_import(filename, content)
    return {"ok": True, "message": "文件已暂存并完成逐行核对", "batch": batch}


@router.post("/import/{batch_id}/commit")
def commit_import(batch_id: int) -> dict[str, Any]:
    """提交暂存批次并落库。

    落库全程加锁：并发导入同一运单只允许一条记录落库，冲突行保留原记录，
    重复提交不生成重复运单；落库后批次汇总、发运单列表与明细同时重算。
    """
    batch, message = service.commit_import(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": True, "message": message, "batch": batch}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出发运单管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "shipment", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条发运单明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"发运单 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条发运单，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="发运单已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条发运单执行确认发运、确认到达、退回货物；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
