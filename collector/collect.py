"""采集编排:发现可选分支、按修订续采、保留矛盾与出处。

原则:
- 只采集页面允许选择的组合;没有测试依据的组合如实记录,不推断兼容;
- 每条观察都要求 页面实际选择 == 组合标识 == 适配修订 三者一致,
  不一致就重走选择过程,仍不一致则记为未完成尝试,绝不记成"不支持";
- 发布方更正(修订上升)时重新检查该卡型的全部组合,
  其他卡型的稳定资料不动;
- 同名下载文件按组合与修订分目录保存。
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from collector.portal import PortalSession, SelectionError
from collector.status import is_contradiction
from collector.store import now


@dataclass(frozen=True)
class Combo:
    card: str
    driver: str
    framework: str
    precision: str

    @property
    def identity(self):
        return '|'.join((self.card, self.driver, self.framework, self.precision))


@dataclass(frozen=True)
class Attention:
    card: str
    model: str
    framework: str
    precision: str


def load_request(path):
    """读取同事给的关注范围(inputs/request.json 格式)。

    返回 (关注条目列表, 是否允许自动跟进新框架)。
    """
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    entries = [
        Attention(
            card=str(a['card']),
            model=str(a.get('model', '')),
            framework=str(a['framework']),
            precision=str(a['precision']))
        for a in data.get('attention', [])
    ]
    allow_new = bool(data.get('allow_new_framework_discovery', True))
    return entries, allow_new


class Collector:
    def __init__(self, portal_url, store, downloads_dir, *,
                 attention=(), attention_only=False,
                 allow_new_framework_discovery=True, full=False,
                 headed=False, settle_timeout_ms=15000,
                 after_summary_hook=None):
        self.portal_url = portal_url
        self.store = store
        self.downloads_dir = Path(downloads_dir)
        self.attention = list(attention)
        self.attention_only = attention_only
        self.allow_new_framework_discovery = allow_new_framework_discovery
        self.full = full
        self.headed = headed
        self.settle_timeout_ms = settle_timeout_ms
        # 演练钩子:读到汇总后、进详情前触发,用于模拟"采集中发布方更正"。
        self.after_summary_hook = after_summary_hook
        self._current_revision = {}

    # ---- 一次完整采集运行 ----

    def run(self):
        from playwright.sync_api import sync_playwright

        note = json.dumps({
            'attention_only': self.attention_only,
            'full': self.full,
            'allow_new_framework_discovery': self.allow_new_framework_discovery,
            'attention': [a.__dict__ for a in self.attention],
        }, ensure_ascii=False)
        run_id = self.store.start_run(self.portal_url, note)
        self.store.set_attention(self.attention)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=not self.headed, args=['--no-sandbox'])
                context = browser.new_context(accept_downloads=True)
                try:
                    session = PortalSession(
                        context.new_page(), self.portal_url, self.settle_timeout_ms)
                    self._collect(session, run_id)
                finally:
                    context.close()
                    browser.close()
        finally:
            self.store.finish_run(run_id)
        return run_id

    def _collect(self, session, run_id):
        session.open()
        space = self._discover(session, run_id)
        queue = deque(self._plan(space, run_id))
        queued = set(queue)
        collected = {}
        while queue:
            combo = queue.popleft()
            queued.discard(combo)
            current = self._current_revision.get(combo.card)
            if current is not None and collected.get(combo) == current:
                continue
            revision = self.collect_combo(session, combo, run_id)
            if revision is None:
                continue
            collected[combo] = revision
            if revision > self._current_revision.get(combo.card, 0):
                # 采集过程中发布方更正了该卡:依赖旧结论的同卡组合都要重查,
                # 其他卡型的稳定资料不受影响。
                self._current_revision[combo.card] = revision
                for other in space:
                    if (other.card == combo.card and other != combo
                            and collected.get(other) != revision
                            and other not in queued):
                        queue.append(other)
                        queued.add(other)

    # ---- 发现:枚举页面当前允许选择的全部分支 ----

    def _discover(self, session, run_id):
        space = []
        self._current_revision = {}
        for card in session.card_options():
            session.select(card=card)
            options = session.options()
            summary = session.summary()
            self._current_revision[card] = summary.revision
            self.store.record_revision_if_new(card, summary.revision, run_id)
            for driver in options.drivers:
                for framework in options.frameworks:
                    for precision in options.precisions:
                        combo = Combo(card, driver, framework, precision)
                        self.store.see_combination(combo, run_id)
                        space.append(combo)
        self.store.mark_unseen_not_selectable(run_id)
        return space

    # ---- 计划:哪些组合本次需要采集 ----

    def _matches_attention(self, combo):
        return any(
            a.card == combo.card and a.framework == combo.framework
            and a.precision == combo.precision
            for a in self.attention)

    def _plan(self, space, run_id):
        blocked_frameworks = set()
        if not self.allow_new_framework_discovery:
            blocked_frameworks = self.store.frameworks_first_seen_in(run_id)
        due = []
        for combo in space:
            if self.attention_only and not self._matches_attention(combo):
                continue
            if combo.framework in blocked_frameworks:
                continue  # 配置不允许自动跟进新框架,报告里仍会体现该分支
            last = self.store.latest_observation(combo)
            current = self._current_revision.get(combo.card)
            if self.full or last is None or last['revision'] != current:
                due.append(combo)
        # 关注清单优先,其余按发现顺序。
        due.sort(key=lambda c: not self._matches_attention(c))
        return due

    # ---- 采集单个组合 ----

    def collect_combo(self, session, combo, run_id, max_attempts=3):
        """采集一个组合,成功返回观察到的修订号,失败返回 None。

        每次尝试都重新走完整选择过程;汇总与详情的标识/修订不一致时
        重试(页面可能在采集中被更正),达到上限仍未一致则只留尝试记录。
        """
        for _ in range(max_attempts):
            try:
                session.open()
                session.select(
                    card=combo.card, driver=combo.driver,
                    framework=combo.framework, precision=combo.precision)
            except SelectionError as exc:
                self.store.record_attempt(run_id, combo, exc.reason, exc.evidence)
                return None
            options = session.options()  # 当时可选项快照,作为出处的一部分
            summary = session.summary()
            if self.after_summary_hook:
                self.after_summary_hook(combo, summary)
            session.open_detail()
            detail = session.detail()
            if (summary.identity != combo.identity
                    or detail.identity != combo.identity
                    or summary.revision != detail.revision):
                self.store.record_attempt(
                    run_id, combo, 'inconsistent',
                    f'选择 {combo.identity};汇总 {summary.identity}@修订{summary.revision};'
                    f'详情 {detail.identity}@修订{detail.revision}')
                self.store.record_revision_if_new(
                    combo.card, max(summary.revision, detail.revision), run_id)
                continue
            dest = (self.downloads_dir
                    / f'card={combo.card}' / f'driver={combo.driver}'
                    / f'framework={combo.framework}' / f'precision={combo.precision}'
                    / f'revision={detail.revision}')
            path, sha, _ = session.download_original(dest)
            dl_revision, dl_identity = self._check_download(path)
            consistent = dl_revision == detail.revision and dl_identity == detail.identity
            if not consistent:
                self.store.record_attempt(
                    run_id, combo, 'download_mismatch',
                    f'下载内容 {dl_identity}@修订{dl_revision} 与详情 '
                    f'{detail.identity}@修订{detail.revision} 不一致')
            contradiction = is_contradiction(
                summary.status, detail.status, detail.operator_status)
            self.store.record_observation(
                run_id=run_id,
                collected_at=now(),
                card=combo.card,
                driver=combo.driver,
                framework=combo.framework,
                precision=combo.precision,
                identity=detail.identity,
                revision=detail.revision,
                summary_status=summary.status,
                detail_status=detail.status,
                build=detail.build,
                model=detail.model,
                operator_name=detail.operator_name,
                operator_status=detail.operator_status,
                conditions=detail.conditions,
                contradiction=1 if contradiction else 0,
                options_json=json.dumps(options.as_dict(), ensure_ascii=False),
                download_path=str(path.relative_to(self.downloads_dir)),
                sha256=sha,
                download_revision=dl_revision,
                consistent=1 if consistent else 0,
            )
            return detail.revision
        self.store.record_attempt(
            run_id, combo, 'give_up', f'{max_attempts} 次尝试后页面仍不一致')
        return None

    @staticmethod
    def _check_download(path):
        """交叉核对下载文件内容里的组合标识与修订(文件是已下载的证据)。"""
        try:
            data = json.loads(Path(path).read_bytes())
            return int(data.get('revision', -1)), str(data.get('combination', ''))
        except Exception:
            return -1, ''
