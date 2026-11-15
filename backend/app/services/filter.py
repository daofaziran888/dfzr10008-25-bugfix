"""数据筛选引擎业务规则：状态流转、字段校验、筛选口径与结果集快照都收在这里。

链路约定：
- 登记/修改模板时，筛选条件、排序字段、分页配置、结果格式与三个必填字段一起落库；
- 列表支持按模板编号、模板名称、筛选条件、目标数据源组合过滤；
- 启用 → 暂停 → 废弃 只能按序推进，模板状态随动作同步；
- 启用动作会按模板配置跑出一份筛选结果集快照，同一模板重复启用只保留一份；
- 本批次筛选失败（坏批次）时保留上一份可用快照，绝不冲掉它。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "filter"
SOURCE_MODULE = "orders"
# 模板上需要持久化的全部业务字段：缺任何一个都会导致下游结果格式/口径丢失
TEMPLATE_FIELDS = [
    "模板编号",
    "模板名称",
    "目标数据源",
    "筛选条件",
    "排序字段",
    "分页配置",
    "结果格式",
]
REQUIRED_FIELDS = ["模板编号", "模板名称", "目标数据源"]
# 列表页可检索的字段：前端按这些中文名传查询参数
SEARCH_FIELDS = ["模板编号", "模板名称", "筛选条件", "目标数据源"]
STATUS_ORDER = ["草稿", "已启用", "已暂停", "已废弃"]
# 动作 -> (允许执行的当前状态, 目标状态)：保证按序推进，模板状态随动作变化
ACTION_RULES = {
    "启用模板": ({"草稿", "已暂停"}, "已启用"),
    "暂停模板": ({"已启用"}, "已暂停"),
    "废弃模板": ({"已暂停"}, "已废弃"),
}
RESULT_FORMATS = {"table", "json"}
DEFAULT_FORMAT = "table"
# 筛选条件解析口径：字段 + 比较符 + 值，例如 store_id=88；无法解析即判定为坏批次
_CONDITION_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(=|!=|>=|<=|>|<)\s*(.+?)\s*$")
_COMPARATORS = {
    "=": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
    ">": lambda left, right: left > right,
    "<": lambda left, right: left < right,
    ">=": lambda left, right: left >= right,
    "<=": lambda left, right: left <= right,
}


class FilterBatchError(RuntimeError):
    """筛选批次执行失败：条件/排序/分页/格式配置无法产出结果集。"""


class FilterService:
    # ------------------------------------------------------------------ 列表/详情
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """按模板编号/名称/筛选条件/数据源做包含匹配，多个条件取交集。"""
        rows = [self._serialize(row) for row in store.rows(MODULE)]
        for field in SEARCH_FIELDS:
            keyword = (filters or {}).get(field, "").strip()
            if keyword:
                rows = [row for row in rows if keyword in str(row.get(field, ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._serialize(entry) if entry is not None else None

    def get_result(self, entry_id: int) -> dict[str, Any] | None:
        """下游分析入口读取筛选结果集：没有快照时明确返回空，不退回默认格式。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        snapshot = entry.get("snapshot")
        if not isinstance(snapshot, dict):
            return {
                "模板编号": entry.get("模板编号"),
                "结果格式": entry.get("结果格式") or DEFAULT_FORMAT,
                "total": 0,
                "items": [],
                "available": False,
            }
        return dict(snapshot)

    # ------------------------------------------------------------------ 登记/修改
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        # 全字段落库：不再只留编号、名称、数据源三个必填项
        for field in TEMPLATE_FIELDS:
            entry[field] = str(values.get(field) or "").strip()
        entry["结果格式"] = self._normalize_format(entry["结果格式"])
        entry["status"] = STATUS_ORDER[0]
        entry["模板状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["snapshot"] = None
        rows.append(entry)
        return self._serialize(entry), []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"筛选模板 {entry_id} 不存在或已归档"
        if entry["status"] == STATUS_ORDER[-1]:
            return None, "已废弃模板不能修改，请重新登记"
        merged = {**entry, **values}
        missing = [field for field in REQUIRED_FIELDS if not str(merged.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        for field in TEMPLATE_FIELDS:
            if field in values:
                entry[field] = str(values.get(field) or "").strip()
        entry["结果格式"] = self._normalize_format(entry["结果格式"])
        # 保留上一份可用快照：新配置在下次启用/重跑成功后才替换它，坏批次冲不掉
        return self._serialize(entry), "筛选模板配置已更新"

    # ------------------------------------------------------------------ 状态流转
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"筛选模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据筛选引擎可执行范围"
        allowed_status, target_status = ACTION_RULES[action]
        current = str(entry.get("status") or "")
        if current not in allowed_status:
            return None, f"模板当前为「{current}」，不允许执行「{action}」，请按草稿→已启用→已暂停→已废弃的顺序推进"

        if action == "启用模板":
            # 重复启用同一模板：重新跑批次，成功后仍然只有一份快照
            try:
                snapshot = self._run_batch(entry)
            except FilterBatchError as exc:
                # 坏批次：状态不切换，上一份可用快照原样保留
                entry["abnormal"] = True
                if entry.get("snapshot"):
                    return None, f"本次筛选批次失败（{exc}），已保留上一份可用结果集"
                return None, f"筛选批次失败（{exc}），暂无可用结果集"
            entry["snapshot"] = snapshot
            entry["abnormal"] = False
        else:
            entry["abnormal"] = action == "废弃模板"

        entry["status"] = target_status
        entry["模板状态"] = target_status
        entry["pending"] = target_status != STATUS_ORDER[-1]
        return self._serialize(entry), f"筛选模板已{action}"

    def rerun_batch(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """手动重跑筛选批次（下游分析入口）：坏批次不覆盖可用快照。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"筛选模板 {entry_id} 不存在或已归档"
        try:
            snapshot = self._run_batch(entry)
        except FilterBatchError as exc:
            entry["abnormal"] = True
            if entry.get("snapshot"):
                return None, f"本次筛选批次失败（{exc}），已保留上一份可用结果集"
            return None, f"筛选批次失败（{exc}），暂无可用结果集"
        entry["snapshot"] = snapshot
        entry["abnormal"] = False
        return self._serialize(entry), "筛选结果集已重新生成"

    def stats(self) -> dict[str, int]:
        rows = store.rows(MODULE)
        return {
            "total": len(rows),
            "enabled": sum(1 for row in rows if row.get("status") == "已启用"),
            "paused": sum(1 for row in rows if row.get("status") == "已暂停"),
        }

    # ------------------------------------------------------------------ 筛选执行
    def _run_batch(self, entry: dict[str, Any]) -> dict[str, Any]:
        """按模板配置对目标数据源执行筛选，产出一份快照；任何配置问题都抛 FilterBatchError。"""
        source_name = str(entry.get("目标数据源") or "").strip()
        source_rows = store.rows(SOURCE_MODULE)
        if source_name != "订单表":
            raise FilterBatchError(f"目标数据源「{source_name}」未接入")

        conditions = self._parse_conditions(str(entry.get("筛选条件") or ""))
        matched = [row for row in source_rows if self._match_all(row, conditions)]

        sort_field, descending = self._parse_sort(str(entry.get("排序字段") or ""))
        if sort_field:
            try:
                matched.sort(key=lambda row: row[sort_field], reverse=descending)
            except (KeyError, TypeError) as exc:
                raise FilterBatchError(f"排序字段「{sort_field}」无法排序：{exc}") from exc

        page_size = self._parse_page_size(entry.get("分页配置"))
        page = matched[:page_size]

        result_format = self._normalize_format(entry.get("结果格式"))
        if result_format not in RESULT_FORMATS:
            # 规范化理论上已兜底，这里再挡一道，坏格式绝不产出默认 table 快照
            raise FilterBatchError(f"结果格式「{result_format}」不受支持")

        return {
            "模板编号": entry.get("模板编号"),
            "目标数据源": source_name,
            "筛选条件": entry.get("筛选条件") or "",
            "排序字段": entry.get("排序字段") or "",
            "分页配置": entry.get("分页配置") or "",
            "结果格式": result_format,
            "total": len(matched),
            "page_size": page_size,
            "items": page,
            "available": True,
        }

    def _parse_conditions(self, raw: str) -> list[tuple[str, str, Any]]:
        text = raw.strip()
        if not text:
            return []
        conditions: list[tuple[str, str, Any]] = []
        for part in re.split(r"[；;]", text):
            part = part.strip()
            if not part:
                continue
            match = _CONDITION_RE.match(part)
            if not match:
                raise FilterBatchError(f"筛选条件「{part}」格式无法解析，示例：store_id=88")
            field, operator, value_text = match.groups()
            conditions.append((field, operator, self._coerce(value_text)))
        return conditions

    @staticmethod
    def _coerce(value_text: str) -> Any:
        value_text = value_text.strip().strip("'\"")
        if re.fullmatch(r"-?\d+", value_text):
            return int(value_text)
        if re.fullmatch(r"-?\d+\.\d+", value_text):
            return float(value_text)
        return value_text

    def _match_all(self, row: dict[str, Any], conditions: list[tuple[str, str, Any]]) -> bool:
        for field, operator, expected in conditions:
            if field not in row:
                raise FilterBatchError(f"筛选字段「{field}」在目标数据源中不存在")
            actual = row[field]
            try:
                if not _COMPARATORS[operator](actual, expected):
                    return False
            except TypeError as exc:
                raise FilterBatchError(f"条件 {field}{operator}{expected} 类型不可比较") from exc
        return True

    @staticmethod
    def _parse_sort(raw: str) -> tuple[str, bool]:
        text = raw.strip()
        if not text:
            return "", False
        parts = text.split()
        field = parts[0]
        direction = parts[1].lower() if len(parts) > 1 else "asc"
        if direction not in {"asc", "desc"}:
            raise FilterBatchError(f"排序方向「{parts[1]}」只能是 asc 或 desc")
        return field, direction == "desc"

    @staticmethod
    def _parse_page_size(raw: Any) -> int:
        if isinstance(raw, int):
            size = raw
        else:
            match = re.search(r"\d+", str(raw or ""))
            if not match:
                raise FilterBatchError(f"分页配置「{raw}」缺少每页条数，示例：20 条/页")
            size = int(match.group())
        if size <= 0:
            raise FilterBatchError("每页条数必须大于 0")
        return min(size, 200)

    @staticmethod
    def _normalize_format(raw: Any) -> str:
        text = str(raw or "").strip().lower()
        return text if text in RESULT_FORMATS else DEFAULT_FORMAT

    @staticmethod
    def _serialize(entry: dict[str, Any]) -> dict[str, Any]:
        """对外视图：模板状态与内部状态保持一致，并带上快照可用性标记。"""
        data = dict(entry)
        data["模板状态"] = entry.get("status")
        data["hasSnapshot"] = isinstance(entry.get("snapshot"), dict)
        data.pop("snapshot", None)
        return data


service = FilterService()
