"""数据筛选引擎接口：维护筛选模板，覆盖启用模板、暂停模板、废弃模板等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.filter import SEARCH_FIELDS, FilterService

router = APIRouter(prefix="/api/filter", tags=["数据筛选引擎"])

service = FilterService()

LIST_FIELDS = ["模板编号", "模板名称", "目标数据源", "筛选条件", "排序字段", "分页配置", "结果格式", "模板状态"]
STATUSES = ["草稿", "已启用", "已暂停", "已废弃"]


def _collect_filters(
    keyword: str | None,
    模板编号: str | None,
    模板名称: str | None,
    筛选条件: str | None,
    目标数据源: str | None,
) -> dict[str, str]:
    """把前端按中文名提交的检索词收敛成一份过滤字典；keyword 兼容旧的编号检索。"""
    filters = {
        "模板编号": 模板编号 or "",
        "模板名称": 模板名称 or "",
        "筛选条件": 筛选条件 or "",
        "目标数据源": 目标数据源 or "",
    }
    if keyword:
        filters["模板编号"] = keyword
    return {key: value for key, value in filters.items() if value.strip()}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="兼容旧参数：按模板编号检索"),
    status: str | None = Query(default=None, description="草稿、已启用、已暂停、已废弃"),
    模板编号: str | None = Query(default=None, alias="模板编号"),
    模板名称: str | None = Query(default=None, alias="模板名称"),
    筛选条件: str | None = Query(default=None, alias="筛选条件"),
    目标数据源: str | None = Query(default=None, alias="目标数据源"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模板编号、模板名称、筛选条件、目标数据源组合过滤；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    filters = _collect_filters(keyword, 模板编号, 模板名称, 筛选条件, 目标数据源)
    items, total = service.list_entries(filters=filters, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def get_stats() -> dict[str, int]:
    """列表页统计卡片：总数、已启用、已暂停。"""
    return service.stats()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据筛选引擎清单：返回当前全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "filter", "total": total, "items": items}


@router.get("/{entry_id}/result")
def get_result(entry_id: int) -> dict[str, Any]:
    """下游分析入口：读取模板最新一份筛选结果集快照（坏批次不会冲掉它）。"""
    result = service.get_result(entry_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"筛选模板 {entry_id} 不存在或已归档")
    return result


@router.post("/{entry_id}/rerun")
def rerun_batch(entry_id: int) -> ActionResult:
    """手动重跑筛选批次；失败时保留上一份可用快照。"""
    entry, message = service.rerun_batch(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条筛选模板明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"筛选模板 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条筛选模板，全部配置字段一起落库，缺必填字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="筛选模板已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改模板配置（筛选条件、排序、分页、结果格式等）；已废弃模板拒绝修改。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条筛选模板执行启用模板、暂停模板、废弃模板；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
