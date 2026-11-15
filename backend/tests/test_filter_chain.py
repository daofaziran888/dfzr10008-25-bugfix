"""筛选模板全链路回归：登记落库 → 列表过滤 → 详情/结果集 → 状态流转 → 坏批次保护。

只用标准库，直接在内存仓库上验证业务规则；每个用例自建模板，避免依赖种子数据的顺序。
运行：backend/.venv/bin/python -m unittest tests.test_filter_chain -v
"""
from __future__ import annotations

import unittest

from app.services.filter import FilterBatchError, FilterService
from app.store import store

MODULE = "filter"


class FilterChainTest(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例拿一个全新服务实例；仓库仍是进程级单例，用唯一编号隔离
        self.service = FilterService()
        self.serial = len(store.rows(MODULE)) + 100

    def _create(self, **overrides):
        self.serial += 1
        values = {
            "模板编号": f"FILT-TEST-{self.serial}",
            "模板名称": f"门店88回归用例{self.serial}",
            "目标数据源": "订单表",
            "筛选条件": "store_id=88",
            "排序字段": "amount desc",
            "分页配置": "20 条/页",
            "结果格式": "table",
        }
        values.update(overrides)
        entry, missing = self.service.create_entry(values)
        self.assertEqual(missing, [])
        assert entry is not None
        return entry

    def test_create_persists_all_config_fields(self):
        """FILT-0002 缺陷：保存后筛选条件/排序/分页/结果格式不能丢。"""
        entry = self._create()
        self.assertEqual(entry["筛选条件"], "store_id=88")
        self.assertEqual(entry["排序字段"], "amount desc")
        self.assertEqual(entry["分页配置"], "20 条/页")
        self.assertEqual(entry["结果格式"], "table")
        self.assertEqual(entry["模板状态"], "草稿")
        # 仓库里的原始记录同样保留全字段，而不是只剩三个必填项
        stored = store.find(MODULE, entry["id"])
        assert stored is not None
        for field in ("筛选条件", "排序字段", "分页配置", "结果格式"):
            self.assertTrue(str(stored.get(field)).strip(), f"{field} 未持久化")

    def test_list_filters_by_name_condition_and_source(self):
        """列表页筛选错位：按模板名称、筛选条件、数据源都应只返回命中的模板。"""
        target = self._create()
        self._create(模板名称="其他门店用例", 筛选条件="store_id=66")

        by_name, total = self.service.list_entries(filters={"模板名称": "门店88回归"})
        self.assertIn(target["id"], [row["id"] for row in by_name])
        self.assertTrue(all("门店88" in row["模板名称"] for row in by_name))
        self.assertEqual(total, len(by_name))

        by_condition, _ = self.service.list_entries(filters={"筛选条件": "store_id"})
        self.assertIn(target["id"], [row["id"] for row in by_condition])

        by_source, _ = self.service.list_entries(filters={"目标数据源": "订单表"})
        self.assertIn(target["id"], [row["id"] for row in by_source])

        empty, empty_total = self.service.list_entries(filters={"模板名称": "不存在的模板XYZ"})
        self.assertEqual(empty, [])
        self.assertEqual(empty_total, 0)

    def test_enable_generates_filtered_snapshot_with_configured_format(self):
        """启用后结果集只含 store_id=88 的行，并保留 table 格式，不退回默认格式。"""
        entry = self._create()
        enabled, message = self.service.run_action(entry["id"], "启用模板")
        assert enabled is not None
        self.assertEqual(enabled["status"], "已启用")
        self.assertEqual(enabled["模板状态"], "已启用")
        self.assertTrue(enabled["hasSnapshot"])

        result = self.service.get_result(entry["id"])
        assert result is not None
        self.assertEqual(result["结果格式"], "table")
        self.assertEqual(result["目标数据源"], "订单表")
        self.assertGreaterEqual(result["total"], 1)
        self.assertTrue(all(row["store_id"] == 88 for row in result["items"]))
        # amount desc 排序要生效
        amounts = [row["amount"] for row in result["items"]]
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_repeated_enable_keeps_single_snapshot(self):
        """启用→暂停→再启用：同一模板始终只有一份结果集，后一份覆盖前一份。"""
        entry = self._create()
        first, _ = self.service.run_action(entry["id"], "启用模板")
        assert first is not None
        stored = store.find(MODULE, entry["id"])
        assert stored is not None
        first_snapshot = stored["snapshot"]
        self.assertIsInstance(first_snapshot, dict)

        # 已启用状态不能重复启用，必须先暂停
        blocked, _ = self.service.run_action(entry["id"], "启用模板")
        self.assertIsNone(blocked)
        self.assertIs(stored["snapshot"], first_snapshot)

        paused, _ = self.service.run_action(entry["id"], "暂停模板")
        assert paused is not None
        self.assertEqual(paused["status"], "已暂停")
        re_enabled, _ = self.service.run_action(entry["id"], "启用模板")
        assert re_enabled is not None
        self.assertEqual(re_enabled["status"], "已启用")
        # 快照被新批次替换，但模板名下仍然只有一份
        self.assertIsInstance(stored["snapshot"], dict)
        self.assertIsNot(stored["snapshot"], first_snapshot)
        self.assertTrue(all(row["store_id"] == 88 for row in stored["snapshot"]["items"]))

    def test_bad_batch_does_not_destroy_good_snapshot(self):
        """坏批次失败不能删掉上一份可用结果。"""
        entry = self._create()
        enabled, _ = self.service.run_action(entry["id"], "启用模板")
        assert enabled is not None
        good_snapshot = store.find(MODULE, entry["id"])["snapshot"]

        # 把配置改坏（引用不存在的字段），重跑批次
        updated, message = self.service.update_entry(entry["id"], {"筛选条件": "ghost_field=88"})
        self.assertIsNotNone(updated)
        self.assertTrue(updated["hasSnapshot"], "配置修改不得冲掉可用快照")
        failed, message = self.service.rerun_batch(entry["id"])
        self.assertIsNone(failed)
        self.assertIn("失败", message)
        # 上一份可用快照原样保留，内容仍是 store_id=88
        kept = store.find(MODULE, entry["id"])["snapshot"]
        self.assertIs(kept, good_snapshot)
        self.assertTrue(all(row["store_id"] == 88 for row in kept["items"]))

    def test_bad_batch_on_first_enable_blocks_status_change(self):
        """首次启用就失败：状态留在草稿，不产出快照，模板状态不被错误切换。"""
        entry = self._create(筛选条件="ghost_field=88")
        failed, _ = self.service.run_action(entry["id"], "启用模板")
        self.assertIsNone(failed)
        stored = store.find(MODULE, entry["id"])
        self.assertEqual(stored["status"], "草稿")
        self.assertIsNone(stored["snapshot"])
        self.assertTrue(stored["abnormal"])

    def test_state_machine_only_advances_in_order(self):
        """启用/暂停/废弃按序推进，模板状态必须随动作变化。"""
        entry = self._create()
        # 草稿不能直接暂停或废弃
        self.assertIsNone(self.service.run_action(entry["id"], "暂停模板")[0])
        self.assertIsNone(self.service.run_action(entry["id"], "废弃模板")[0])
        self.assertEqual(store.find(MODULE, entry["id"])["status"], "草稿")

        self.service.run_action(entry["id"], "启用模板")
        # 已启用不能废弃，只能先暂停
        self.assertIsNone(self.service.run_action(entry["id"], "废弃模板")[0])
        self.assertEqual(store.find(MODULE, entry["id"])["status"], "已启用")

        self.service.run_action(entry["id"], "暂停模板")
        self.assertEqual(store.find(MODULE, entry["id"])["status"], "已暂停")
        self.service.run_action(entry["id"], "废弃模板")
        self.assertEqual(store.find(MODULE, entry["id"])["status"], "已废弃")
        # 终态后任何动作都被拒绝
        self.assertIsNone(self.service.run_action(entry["id"], "启用模板")[0])
        self.assertEqual(store.find(MODULE, entry["id"])["status"], "已废弃")

    def test_pagination_and_missing_field_guard(self):
        """分页配置生效；登记缺必填字段被明确拒绝。"""
        entry = self._create(分页配置="3 条/页")
        self.service.run_action(entry["id"], "启用模板")
        result = self.service.get_result(entry["id"])
        self.assertEqual(len(result["items"]), 3)
        self.assertGreaterEqual(result["total"], 3)

        bad, missing = self.service.create_entry({"模板编号": "", "模板名称": "", "目标数据源": ""})
        self.assertIsNone(bad)
        self.assertEqual(set(missing), {"模板编号", "模板名称", "目标数据源"})

    def test_unsupported_source_is_bad_batch(self):
        """未接入的数据源属于坏批次，不能静默产出空表冒充成功。"""
        entry = self._create(目标数据源="不存在的表")
        with self.assertRaises(FilterBatchError):
            self.service._run_batch(store.find(MODULE, entry["id"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
