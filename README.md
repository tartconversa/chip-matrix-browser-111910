# 算力适配门户

这是本地生成的外部来源与独立接收端，仅用于提供数据、页面、协议与回执。仓库提供合成来源服务，采集/RPA客户端待开发。所有单位、厂商、研究记录和门户内容均为虚构数据，不连接真实业务系统。

## 启动

安装Docker后，在项目根目录运行 docker compose up --build，服务默认监听 http://127.0.0.1:8765。若直接在主机运行，Python 3.11+执行 pip install -r requirements.txt，再运行 python -m source.app --port 8765。浏览器任务通过Playwright 1.56.0提供的Chromium测试来源页面。

输入示例位于 inputs/request.json；业务字段、页面与协议边界见 docs/source-contract.md。示例是项目可读输入格式，原件与来源修订均由演练服务产生。

## 来源说明

服务页面 / 简述当前来源。JSON与XML接口描述输入契约，页面展示来源交互；POST /__control 只用于本地演练人员切换来源修订、可用性和延迟场景。状态可用 --state /path/outside/source-state.json 选择性落盘，默认每次启动恢复初始样本。端点行为和合成记录见 source/app.py。

## 检查

运行 python -m unittest discover -s tests -v 检查来源端、原件、门户状态和合成接收端本身。涉及页面的数据源测试会启动真实Chromium。它不验证任何尚未编写的采集程序，也不代表采集业务验收已经通过。

## 采集程序

collector/ 是已实现的 Python + Chromium 采集客户端，只操作用户可见的控件、详情与下载（边界见 docs/source-contract.md），把组合证据落入本地 SQLite 资料库，重启后续采。

```bash
# 采集（关注范围可选；默认发现页面全部分支并采集）
python3 -m collector collect --portal http://127.0.0.1:8765 --attention inputs/request.json
# 按组合查询最新结论及出处（当时选项、子项、文件摘要）
python3 -m collector query --card c1 --driver d10 --framework infer --precision fp16
# 总览：已观察/仍无测试依据/矛盾/新出现分支
python3 -m collector report
# 关注清单逐项核对
python3 -m collector attention --attention inputs/request.json
```

行为要点：只采集页面允许选择的组合；"不支持""尚未测"按发布方原文记录，"当前筛选没有记录"是资料库查询结果，三者不混；汇总与子项矛盾时双方原文都保留；驱动别名与实际构建号分别记录，版本号按段比较；发布方更正（修订上升）只重查该卡型的组合；同名下载按组合与修订分目录保存；选择过程未完成只留尝试记录，绝不记成"不支持"。资料库与下载默认写入 var/（可用 --db、--downloads 更改）。

运行 python -m unittest discover -s tests -v 会同时用真实 Chromium 检查采集程序的控件联动、版本更正与未测状态处理。
