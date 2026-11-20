"""版本与构建号的段式处理。

驱动构建号(如 10.12.3+build7)不是小数:10.12.3 与 10.9.1
按小数比较会得出 10.9.1 更大的错误结论。这里一律拆成
数值段与构建后缀,逐段比较,原始字符串同时保留用于展示。
"""
from __future__ import annotations

import re

_SEGMENT = re.compile(r'\d+|[a-zA-Z]+')


def parse_build(text):
    """把 '10.12.3+build7' 拆成 ((10, 12, 3), ('build', 7))。

    主版本号按点分段转整数;'+“ 后的构建后缀拆成数字/字母交替的段。
    无法识别的部分按原文保留,不丢信息。
    """
    text = (text or '').strip()
    main, _, suffix = text.partition('+')
    nums = tuple(int(p) for p in main.split('.') if p != '')
    meta = tuple(int(t) if t.isdigit() else t for t in _SEGMENT.findall(suffix))
    return nums, meta


def _tagged(meta):
    # 数字段与字母段混合时给排序一个确定规则:数字排前,类型不同不抛错。
    return tuple((0, t) if isinstance(t, int) else (1, t) for t in meta)


def build_key(text):
    """排序用键:数值段逐段比较,构建后缀再逐段比较。绝不按小数比较。"""
    nums, meta = parse_build(text)
    return (nums, _tagged(meta))


def compare_builds(a, b):
    """段式比较两个构建号,a 大返回 1,相等 0,a 小返回 -1。"""
    ka, kb = build_key(a), build_key(b)
    return (ka > kb) - (ka < kb)
