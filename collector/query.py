"""工程师查询:从一项结论回到当时选项、子项记录和文件摘要。

三种含义严格区分:
- "不支持"/"尚未测":发布方在页面上给出的事实,按原文展示;
- "当前筛选没有记录":本地资料库里没有对应观察,细分为
  "组合可选但尚未采集"与"该组合从未作为可选项出现",
  对没有测试依据的组合不推断兼容。
"""
from __future__ import annotations

import json
from collections import Counter

from collector.collect import Combo
from collector.versions import build_key

NO_RECORD = '当前筛选没有记录'


def _combo_of(row):
    return Combo(row['card'], row['driver'], row['framework'], row['precision'])


def format_observation(row):
    """一条结论连同它的全部出处。"""
    lines = [
        f"组合 {row['identity']}(观察 #{row['id']},运行 #{row['run_id']},{row['collected_at']})",
        f"  汇总状态: {row['summary_status']}    详情状态: {row['detail_status']}"
        f"    算子 {row['operator_name']} 子项: {row['operator_status']}",
    ]
    if row['contradiction']:
        lines.append(
            f"  ⚠ 矛盾: 汇总({row['summary_status']})与子项({row['operator_status']})色调不一致,"
            '两者均按页面原文保留,未作取舍')
    lines.append(f"  驱动别名: {row['driver']}    实际构建: {row['build']}")
    proof = f"  适配修订: {row['revision']}"
    if row['consistent']:
        proof += '(页面选择、汇总、详情、下载内容四者一致)'
    else:
        proof += '(下载内容与页面不一致,见尝试记录)'
    lines.append(proof)
    lines.append(f"  测试条件: {row['conditions']}")
    options = json.loads(row['options_json'])
    lines.append(
        f"  当时可选项: 卡型 {options['card']};驱动 {options['drivers']};"
        f"框架 {options['frameworks']};精度 {options['precisions']}")
    if row['download_path']:
        lines.append(f"  原始记录: {row['download_path']}")
        lines.append(
            f"  文件摘要 SHA256: {row['sha256']}(下载内容修订 {row['download_revision']})")
    return '\n'.join(lines)


def query(store, filters):
    """按组合筛选查询最新结论;没有记录时说明是哪一种"没有"。"""
    rows = store.latest_observations(**filters)
    if rows:
        return '\n\n'.join(format_observation(r) for r in rows)
    known = [c for c in store.all_combinations()
             if all(c[k] == v for k, v in filters.items() if k in c)]
    if known:
        state = '可选但尚未采集' if any(c['selectable'] for c in known) else '曾可选,当前已不可选'
        return f'{NO_RECORD}:组合{state},无测试依据(不推断兼容)'
    return f'{NO_RECORD}:该组合从未作为可选项出现,无测试依据(不推断兼容)'


def report(store):
    """资料库总览:已观察、仍无测试依据、矛盾、新分支、未完成尝试。"""
    lines = []
    runs = store.run_list()
    if not runs:
        return '资料库为空:尚未运行过采集'
    last = runs[-1]
    lines.append(f"运行次数: {len(runs)},最近运行 #{last['id']}({last['started_at']})")
    for card in store.cards():
        lines.append(f'卡型 {card}: 已知适配修订 {store.revisions(card)}')
    combos = store.all_combinations()
    selectable = [c for c in combos if c['selectable']]
    latest = []
    missing = []
    for c in selectable:
        obs = store.latest_observation(_combo_of(c))
        (latest if obs else missing).append(obs or c)
    lines.append(f'可选组合 {len(selectable)} 个:已观察 {len(latest)},仍无测试依据 {len(missing)}')
    for c in missing:
        lines.append(f"  无依据: {c['card']}|{c['driver']}|{c['framework']}|{c['precision']}")
    if latest:
        dist = Counter(o['summary_status'] for o in latest)
        lines.append('最近结论分布: ' + ', '.join(f'{k} {v}' for k, v in sorted(dist.items())))
        builds = sorted({o['build'] for o in latest}, key=build_key)
        lines.append(f'观察到的实际构建(段式排序): {builds}')
    contra = [o for o in latest if o['contradiction']]
    lines.append(f'汇总与子项矛盾: {len(contra)} 条(双方原文均保留)')
    for o in contra:
        lines.append(
            f"  {o['identity']} 修订{o['revision']}: 汇总 {o['summary_status']}"
            f" / 算子{o['operator_name']} {o['operator_status']}")
    new_frameworks = store.frameworks_first_seen_in(last['id'])
    if new_frameworks:
        lines.append(f'最近运行新出现的框架分支: {sorted(new_frameworks)}')
    attempts = store.all_attempts()
    lines.append(f'未完成的选择尝试: {len(attempts)} 条(均未记为不支持)')
    for a in attempts:
        lines.append(
            f"  运行 #{a['run_id']} {a['card']}|{a['driver']}|{a['framework']}|{a['precision']}"
            f" {a['outcome']}: {a['evidence']}")
    return '\n'.join(lines)


def attention_report(store, entries=None):
    """关注清单逐项核对:有依据 / 无依据,驱动别名逐个别出。"""
    if entries is None:
        entries = store.attention()
    if not entries:
        return '关注清单为空'
    lines = []
    for e in entries:
        lines.append(
            f"关注组合 卡型{e['card']} / 模型{e['model']} / 框架{e['framework']} / 精度{e['precision']}:")
        combos = store.combinations_matching(e['card'], e['framework'], e['precision'])
        if not combos:
            lines.append('  门户当前没有该可选项,无测试依据(不推断兼容)')
            continue
        for c in combos:
            obs = store.latest_observation(_combo_of(c))
            if obs is None:
                lines.append(f"  驱动 {c['driver']}: 无测试依据(组合可选但尚未采集)")
            elif e['model'] and obs['model'] != e['model']:
                lines.append(
                    f"  驱动 {c['driver']}: 依据模型为 {obs['model']},"
                    f"与关注的 {e['model']} 不符")
            else:
                mark = ' ⚠矛盾' if obs['contradiction'] else ''
                lines.append(
                    f"  驱动 {c['driver']}: 有依据 — 汇总\"{obs['summary_status']}\","
                    f"算子 {obs['operator_name']}\"{obs['operator_status']}\"{mark},"
                    f"修订 {obs['revision']},构建 {obs['build']},"
                    f"观察 #{obs['id']}(运行 #{obs['run_id']})")
    return '\n'.join(lines)
