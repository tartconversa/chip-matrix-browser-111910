"""采集程序的真实浏览器端到端检查。

覆盖:控件联动(切卡重建选项)、状态语义(不支持/尚未测/没有记录)、
版本更正后的重查与矛盾保留、同名下载分修订保存、新框架发现、
重启续采、以及从结论回到出处。均针对本地合成来源,不触碰内部接口。
"""
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from collector.collect import Collector, Combo
from collector.portal import PortalSession
from collector.store import Store
from collector.versions import compare_builds, parse_build
from collector import query as query_mod
from tests.support import SourceTest


class VersionTest(unittest.TestCase):
    """版本号按段比较,不当普通小数。"""

    def test_parse_build(self):
        self.assertEqual(parse_build('10.12.3+build7'), ((10, 12, 3), ('build', 7)))
        self.assertEqual(parse_build('4.2.0+build5'), ((4, 2, 0), ('build', 5)))

    def test_segments_not_decimals(self):
        # 按小数 10.91 > 10.123,按段 10.12.3 > 10.9.1(次版本 12 > 9)
        self.assertGreater(compare_builds('10.12.3+build7', '10.9.1+build1'), 0)
        # 按小数 9.109 < 9.91,按段 9.10.9 > 9.9.1
        self.assertGreater(compare_builds('9.10.9+build12', '9.9.1+build1'), 0)
        # 构建后缀也按段:build7 < build12
        self.assertLess(compare_builds('10.12.3+build7', '10.12.3+build12'), 0)
        self.assertEqual(compare_builds('4.2.0+build5', '4.2.0+build5'), 0)


class CollectorBrowserTest(SourceTest):
    def setUp(self):
        super().setUp()
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_path = self.root / 'collector.db'
        self.downloads = self.root / 'downloads'

    def open_store(self):
        return Store(self.db_path)

    def collect(self, **kwargs):
        store = self.open_store()
        try:
            return Collector(self.base, store, self.downloads, **kwargs).run()
        finally:
            store.close()

    def observations(self, store):
        return store.all_observations()

    # ---- 全量采集:组合空间、状态语义、控件联动、下载证据 ----

    def test_full_collection_covers_space_and_linkage(self):
        self.collect()
        store = self.open_store()
        self.addCleanup(store.close)
        combos = store.all_combinations()
        # c1: d10/d9 × infer/train × fp16/int8 = 8;c2: stable × infer/train × fp16/int8 = 4
        self.assertEqual(len(combos), 12)
        self.assertTrue(all(c['selectable'] for c in combos))
        obs = self.observations(store)
        self.assertEqual(len(obs), 12)
        self.assertTrue(all(o['consistent'] for o in obs))
        by_id = {o['identity']: o for o in obs}
        # 发布方事实按原文记录
        self.assertEqual(by_id['c2|stable|train|int8']['summary_status'], '不支持')
        self.assertEqual(by_id['c1|d9|train|int8']['summary_status'], '不支持')
        self.assertEqual(by_id['c1|d10|infer|fp16']['summary_status'], '通过')
        statuses = [o['summary_status'] for o in obs]
        self.assertEqual(statuses.count('不支持'), 3)
        self.assertEqual(statuses.count('通过'), 9)
        self.assertFalse(any(o['contradiction'] for o in obs))
        # 驱动别名 → 详情里的实际构建
        self.assertEqual(by_id['c1|d10|infer|fp16']['build'], '10.12.3+build7')
        self.assertEqual(by_id['c1|d9|infer|fp16']['build'], '9.10.9+build12')
        self.assertEqual(by_id['c2|stable|infer|fp16']['build'], '4.2.0+build5')
        # 控件联动:切到 c2 后选项已重建,快照里没有残留的 d10/d9
        c2_snap = json.loads(by_id['c2|stable|infer|fp16']['options_json'])
        self.assertEqual(c2_snap['drivers'], ['stable'])
        self.assertEqual(c2_snap['frameworks'], ['infer', 'train'])
        c1_snap = json.loads(by_id['c1|d10|infer|fp16']['options_json'])
        self.assertEqual(c1_snap['drivers'], ['d10', 'd9'])
        # 每个组合都有下载的原始记录,摘要与内容、组合标识、修订一致
        for o in obs:
            path = self.downloads / o['download_path']
            self.assertTrue(path.exists(), o['download_path'])
            data = path.read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), o['sha256'])
            payload = json.loads(data)
            self.assertEqual(payload['combination'], o['identity'])
            self.assertEqual(payload['revision'], o['revision'])
            self.assertEqual(payload['revision'], 1)

    # ---- 重启续采:无变化时不做重复工作 ----

    def test_resume_without_changes_is_noop(self):
        self.collect()
        self.collect()
        store = self.open_store()
        self.addCleanup(store.close)
        self.assertEqual(len(store.run_list()), 2)
        self.assertEqual(len(self.observations(store)), 12)

    # ---- 发布方更正:重查该卡、保留矛盾、同名下载分修订保存 ----

    def test_correction_rechecks_only_that_card(self):
        self.collect()
        self.control('correct', card='c1')
        self.collect()
        store = self.open_store()
        self.addCleanup(store.close)
        self.assertEqual(store.revisions('c1'), [1, 2])
        self.assertEqual(store.revisions('c2'), [1])
        obs = self.observations(store)
        c1_obs = [o for o in obs if o['card'] == 'c1']
        c2_obs = [o for o in obs if o['card'] == 'c2']
        # c1 全部组合在修订 2 重新检查;c2 稳定资料不动
        self.assertEqual(len(c1_obs), 16)
        self.assertEqual(len(c2_obs), 4)
        for c in store.all_combinations():
            latest = store.latest_observation(Combo(c['card'], c['driver'], c['framework'], c['precision']))
            self.assertEqual(latest['revision'], 2 if c['card'] == 'c1' else 1)
        # 矛盾保留:汇总"通过"与算子子项"失败"都在,不取颜色较好的一个
        latest_c1 = [o for o in store.latest_observations(card='c1')]
        contradicted = [o for o in latest_c1 if o['summary_status'] == '通过']
        self.assertTrue(contradicted)
        for o in contradicted:
            self.assertEqual(o['operator_status'], '失败')
            self.assertTrue(o['contradiction'])
        # 同名 test-result.bin 按修订分别保存,内容不同
        base = self.downloads / 'card=c1' / 'driver=d10' / 'framework=infer' / 'precision=fp16'
        rev1 = (base / 'revision=1' / 'test-result.bin').read_bytes()
        rev2 = (base / 'revision=2' / 'test-result.bin').read_bytes()
        self.assertNotEqual(hashlib.sha256(rev1).hexdigest(), hashlib.sha256(rev2).hexdigest())
        self.assertEqual(json.loads(rev1)['revision'], 1)
        self.assertEqual(json.loads(rev2)['revision'], 2)

    # ---- 采集中更正:汇总与详情不一致时重走选择过程 ----

    def test_midrun_correction_retries_until_consistent(self):
        fired = []

        def hook(combo, summary):
            if not fired:
                fired.append(combo)
                self.control('correct', card='c1')

        self.collect(after_summary_hook=hook)
        store = self.open_store()
        self.addCleanup(store.close)
        self.assertTrue(fired)
        # 不一致的尝试留下了证据,但没有写进观察
        attempts = store.all_attempts()
        self.assertTrue(any(a['outcome'] == 'inconsistent' for a in attempts))
        obs = self.observations(store)
        self.assertEqual(len(obs), 12)
        self.assertTrue(all(o['consistent'] for o in obs))
        for o in obs:
            if o['card'] == 'c1':
                self.assertEqual(o['revision'], 2)
            else:
                self.assertEqual(o['revision'], 1)

    # ---- 新框架发现与"尚未测"状态 ----

    def test_new_framework_discovery_and_untested(self):
        self.collect()
        self.control('framework')
        run_id = self.collect()
        store = self.open_store()
        self.addCleanup(store.close)
        combos = store.all_combinations()
        self.assertEqual(len(combos), 18)
        self.assertIn('next', store.frameworks_first_seen_in(run_id))
        next_obs = [o for o in self.observations(store) if o['framework'] == 'next']
        self.assertEqual(len(next_obs), 6)
        # "尚未测"是发布方给出的事实,按原文记录,不等于"不支持"
        self.assertTrue(all(o['summary_status'] == '尚未测' for o in next_obs))
        self.assertTrue(all(o['operator_status'] == '尚未测' for o in next_obs))
        self.assertFalse(any(o['contradiction'] for o in next_obs))
        self.assertIn('next', query_mod.report(store))

    # ---- 关注清单:范围限定、不可选不伪造、出现后补采 ----

    def test_attention_scoping_and_late_framework(self):
        attention = [
            {'card': 'c1', 'model': 'm-demo', 'framework': 'infer', 'precision': 'fp16'},
            {'card': 'c1', 'model': 'm-demo', 'framework': 'next', 'precision': 'fp16'},
        ]
        from collector.collect import load_request, Attention
        entries = [Attention(**a) for a in attention]
        # next 框架尚不存在:不伪造任何结论
        self.collect(attention=entries, attention_only=True)
        store = self.open_store()
        self.addCleanup(store.close)
        obs = self.observations(store)
        self.assertEqual(len(obs), 2)  # 只有 c1 × d10/d9 × infer × fp16
        self.assertTrue(all(o['framework'] == 'infer' for o in obs))
        rep = query_mod.attention_report(store)
        self.assertIn('无测试依据', rep)
        self.assertNotIn('不支持', rep)
        # 查询一个可选但未采集的组合:是"没有记录",不是"不支持"
        out = query_mod.query(store, {'card': 'c2', 'driver': 'stable',
                                      'framework': 'train', 'precision': 'int8'})
        self.assertIn(query_mod.NO_RECORD, out)
        self.assertNotIn('不支持', out)
        # 新框架出现后补采,状态为"尚未测"
        self.control('framework')
        store.close()
        self.collect(attention=entries, attention_only=True)
        store = self.open_store()
        rep = query_mod.attention_report(store)
        self.assertIn('尚未测', rep)
        next_obs = [o for o in self.observations(store) if o['framework'] == 'next']
        self.assertEqual(len(next_obs), 2)

    # ---- 未完成的选择过程不能记成"不支持" ----

    def test_failed_selection_not_recorded_as_unsupported(self):
        store = self.open_store()
        self.addCleanup(store.close)
        run_id = store.start_run(self.base, 'whitebox')
        collector = Collector(self.base, store, self.downloads)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
            context = browser.new_context(accept_downloads=True)
            try:
                session = PortalSession(context.new_page(), self.base)
                result = collector.collect_combo(
                    session, Combo('c1', 'd8', 'infer', 'fp16'), run_id)
            finally:
                context.close()
                browser.close()
        store.finish_run(run_id)
        self.assertIsNone(result)
        attempts = store.all_attempts()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]['outcome'], 'option_missing')
        self.assertIn('d8', attempts[0]['evidence'])
        self.assertEqual(self.observations(store), [])

    # ---- 从结论回到出处:当时选项、子项记录、文件摘要 ----

    def test_query_traceability(self):
        self.control('correct', card='c1')
        self.collect()
        store = self.open_store()
        self.addCleanup(store.close)
        out = query_mod.query(store, {'card': 'c1', 'driver': 'd10',
                                      'framework': 'infer', 'precision': 'fp16'})
        for token in ('通过', '失败', '矛盾', '10.12.3+build7', '修订',
                      'SHA256', 'd10', 'd9', '运行 #', 'test-result.bin'):
            self.assertIn(token, out)
        # 不存在的组合:没有记录,且不出现"不支持"
        out = query_mod.query(store, {'card': 'c9'})
        self.assertIn(query_mod.NO_RECORD, out)
        self.assertNotIn('不支持', out)


if __name__ == '__main__':
    unittest.main()
