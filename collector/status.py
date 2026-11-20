"""状态色调归类:只用于发现矛盾,绝不用于替工程师取舍。

页面状态一律按原文存储(通过/不支持/尚未测/失败)。色调只回答
一个问题:汇总与子项是否"颜色不一致"。不一致时两边原文都保留,
查询时同时展示,不取颜色较好的一个。
"""

# 已知状态的色调;未知状态按自身处理,任何差异都会被视为矛盾。
TONE = {
    '通过': 'ok',
    '失败': 'bad',
    '不支持': 'bad',
    '尚未测': 'unknown',
}


def tone(status):
    return TONE.get(status, 'other:' + str(status))


def is_contradiction(summary_status, detail_status, operator_status):
    """汇总状态、详情状态、算子子项三者色调不一致即为矛盾。"""
    tones = {tone(summary_status), tone(detail_status), tone(operator_status)}
    return len(tones) > 1
