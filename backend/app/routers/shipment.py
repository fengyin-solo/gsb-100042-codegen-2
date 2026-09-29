"""发运单管理接口：维护发运单，覆盖批量导入、确认发运、确认到达等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.schemas import (
    ActionResult,
    EntryPayload,
    PageResult,
    ShipmentImportResult,
)
from app.services.shipment import ShipmentService

router = APIRouter(prefix="/api/shipment", tags=["发运单管理"])

service = ShipmentService()

LIST_FIELDS = ["运单编号", "发货方", "收货方", "发运批次", "货物名称", "温层要求", "发运日期", "预计到达", "运单状态"]
STATUSES = ["待发运", "在途", "已到达", "已签收", "已退回"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按运单编号检索"),
    status: str | None = Query(default=None, description="待发运、在途、已到达、已签收、已退回"),
    batch: str | None = Query(default=None, description="按发运批次检索"),
    sender: str | None = Query(default=None, description="按发货方检索"),
    receiver: str | None = Query(default=None, description="按收货方检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按运单编号、三方主体与发运批次过滤发运单列表。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        batch=batch,
        sender=sender,
        receiver=receiver,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/batch-summary")
def batch_summary() -> dict[str, Any]:
    """批次汇总：导入确认后根据当前发运单实时重算。"""
    return {"items": service.batch_summary(), "stats": service.stats()}


@router.get("/imports/{job_id}", response_model=ShipmentImportResult)
def get_import(job_id: str) -> dict[str, Any]:
    job = service.get_import(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="导入任务不存在或暂存文件已过期")
    return job


@router.post("/imports", response_model=ShipmentImportResult)
async def stage_import(file: UploadFile = File(...)) -> dict[str, Any]:
    """第一步：暂存上传文件，逐行核对运单编号、发货方、收货方和发运批次。"""
    content = await file.read()
    try:
        return service.stage_import(file.filename or "shipment.csv", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/imports/{job_id}/commit", response_model=ShipmentImportResult)
def commit_import(job_id: str) -> dict[str, Any]:
    """第二步：确认暂存结果；冲突运单保留原记录，重试不会重复落库。"""
    job, message = service.commit_import(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=message)
    job["message"] = message if job["status"] == "imported" else job["message"]
    return job


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出发运单管理清单：返回当前数据的全量内容。"""
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
