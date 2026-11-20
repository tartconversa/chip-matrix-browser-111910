"""浏览器交互层:只操作用户可见控件、可见文本、可见链接与下载。

边界(与 docs/source-contract.md 一致):
- 只读用户能看到的内容:控件值、选项列表、加载提示、汇总、详情、下载文件;
- 不读取页面 JS 对象,不把浏览器内部请求 /ui/* 当作受支持采集 API;
- 选择过程未完成时抛 SelectionError,由上层记为"未完成尝试",
  绝不能记成"不支持"。
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass

DONE_TEXT = '已完成'
CONTROL_KEYS = ('card', 'driver', 'framework', 'precision')

_REVISION = re.compile(r'(\d+)')
_MODEL = re.compile(r'模型\s*([^\s；;]+)')
_OPERATOR = re.compile(r'算子\s*([^\s；;]+)')


class SelectionError(Exception):
    """选择过程未完成。reason 是机器可读原因,evidence 是页面上的原文证据。"""

    def __init__(self, reason, evidence):
        super().__init__(f'{reason}: {evidence}')
        self.reason = reason
        self.evidence = evidence


@dataclass
class Options:
    """某一时刻页面上实际可选的分支(可见选项快照)。"""

    card: str
    drivers: list
    frameworks: list
    precisions: list

    def as_dict(self):
        return {
            'card': self.card,
            'drivers': list(self.drivers),
            'frameworks': list(self.frameworks),
            'precisions': list(self.precisions),
        }


@dataclass
class Summary:
    identity: str
    revision: int
    status: str
    text: str


@dataclass
class Detail:
    identity: str
    revision: int
    status: str
    build: str
    operator_name: str
    operator_status: str
    conditions: str
    model: str
    original_href: str


def _text(locator):
    return (locator.text_content() or '').strip()


class PortalSession:
    """对门户页面的一次浏览器会话。"""

    def __init__(self, page, base_url, settle_timeout_ms=15000, poll_ms=50):
        self.page = page
        self.base_url = base_url
        self.settle_timeout_ms = settle_timeout_ms
        self.poll_ms = poll_ms

    # ---- 可见控件读取 ----

    def control_values(self):
        return {k: self.page.locator('#' + k).input_value() for k in CONTROL_KEYS}

    def card_options(self):
        return [t.strip() for t in self.page.locator('#card option').all_text_contents()]

    def options(self):
        return Options(
            card=self.page.locator('#card').input_value(),
            drivers=[t.strip() for t in self.page.locator('#driver option').all_text_contents()],
            frameworks=[t.strip() for t in self.page.locator('#framework option').all_text_contents()],
            precisions=[t.strip() for t in self.page.locator('#precision option').all_text_contents()],
        )

    # ---- 同步:等页面真正稳定 ----

    def open(self):
        self.page.goto(self.base_url)
        self.wait_consistent()

    def wait_consistent(self):
        """等到"已完成"且汇总组合标识与四个控件当前值完全一致。

        切卡会重建驱动与框架控件,旧选项可能短暂显示,详情也可能在
        汇总刷新前返回;只有页面报告的组合标识等于实际选择时,才认为
        页面稳定。加载提示不是"已完成"、标识不一致、或页面明确拒绝
        (如"选项不属于当前卡型")时,都不会被误认为结果。
        """
        deadline = time.monotonic() + self.settle_timeout_ms / 1000
        last = '页面未进入稳定状态'
        while time.monotonic() < deadline:
            loading = _text(self.page.locator('#loading'))
            values = self.control_values()
            expected = '|'.join(values[k] for k in CONTROL_KEYS)
            summary = self.page.locator('#summary')
            has_identity = summary.locator('#identity').count() > 0
            if loading == DONE_TEXT and has_identity:
                identity = _text(summary.locator('#identity'))
                if identity == expected:
                    return values
                last = f'汇总标识 {identity!r} 与当前选择 {expected!r} 不一致'
            elif loading == DONE_TEXT:
                text = _text(summary)
                if text:
                    # 页面对当前筛选给出了可见的拒绝原因(不是适配结论)。
                    raise SelectionError('page_rejected', text)
                last = '加载完成但汇总为空'
            else:
                last = f'加载提示:{loading or "(空)"}'
            self.page.wait_for_timeout(self.poll_ms)
        raise SelectionError('settle_timeout', last)

    def select(self, **changes):
        """按 卡型→驱动→框架→精度 的级联顺序选择,每步都等页面稳定。

        只选择页面上真实存在的可见选项;选项不存在时抛 SelectionError。
        """
        for key in CONTROL_KEYS:
            want = changes.get(key)
            if want is None:
                continue
            have = self.page.locator('#' + key).input_value()
            if have != want:
                available = [t.strip() for t in self.page.locator('#' + key + ' option').all_text_contents()]
                if want not in available:
                    raise SelectionError(
                        'option_missing',
                        f'控件 #{key} 当前可选 {available},没有 {want!r}')
                self.page.select_option('#' + key, label=want)
            self.wait_consistent()

    # ---- 汇总与详情(可见文本) ----

    def summary(self):
        """读取当前汇总。调用前必须先 wait_consistent。"""
        s = self.page.locator('#summary')
        return Summary(
            identity=_text(s.locator('#identity')),
            revision=int(_REVISION.search(_text(s.locator('#revision'))).group(1)),
            status=_text(s.locator('#status')),
            text=_text(s),
        )

    def open_detail(self):
        """点击可见的"测试详情"链接进入详情页。"""
        with self.page.expect_navigation():
            self.page.click('#detail')

    def detail(self):
        conditions = ''
        for p in self.page.locator('p').all():
            t = _text(p)
            if t.startswith('条件'):
                conditions = t
                break
        model = _MODEL.search(conditions)
        operator = _OPERATOR.search(conditions)
        return Detail(
            identity=_text(self.page.locator('#identity')),
            revision=int(_REVISION.search(_text(self.page.locator('#revision'))).group(1)),
            status=_text(self.page.locator('#status')),
            build=_text(self.page.locator('#build')).removeprefix('构建').strip(),
            operator_name=operator.group(1) if operator else '',
            operator_status=_text(self.page.locator('#operator')),
            conditions=conditions,
            model=model.group(1) if model else '',
            original_href=self.page.locator('#original').get_attribute('href') or '',
        )

    def download_original(self, dest_dir):
        """点击可见的下载链接保存原始记录。

        同名文件(如 test-result.bin)按调用方给的目录分别保存;
        同目录下内容不同则追加摘要后缀,绝不互相覆盖。
        返回 (路径, sha256, 是否新写入)。
        """
        with self.page.expect_download() as got:
            self.page.click('#original')
        download = got.value
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = download.suggested_filename or 'download.bin'
        tmp = dest_dir / ('.partial-' + os.urandom(6).hex())
        download.save_as(tmp)
        data = tmp.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        target = dest_dir / name
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() == sha:
                tmp.unlink()
                return target, sha, False
            alt = dest_dir / f'{name}.sha256-{sha[:12]}'
            if alt.exists() and hashlib.sha256(alt.read_bytes()).hexdigest() == sha:
                tmp.unlink()
                return alt, sha, False
            tmp.replace(alt)
            return alt, sha, True
        tmp.replace(target)
        return target, sha, True
