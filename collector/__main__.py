"""命令行入口:collect / query / report / attention。"""
from __future__ import annotations

import argparse
import sys

from collector import query as query_mod
from collector.collect import Collector, load_request
from collector.store import Store

DEFAULT_DB = 'var/collector.db'
DEFAULT_DOWNLOADS = 'var/downloads'


def _add_db(p):
    p.add_argument('--db', default=DEFAULT_DB, help=f'资料库路径(默认 {DEFAULT_DB})')


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='python -m collector',
        description='算力适配门户采集程序:只用可见控件、详情与下载,证据入库供查询')
    sub = parser.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('collect', help='采集门户组合证据(可重启续采)')
    c.add_argument('--portal', required=True, help='门户地址,如 http://127.0.0.1:8765')
    _add_db(c)
    c.add_argument('--downloads', default=DEFAULT_DOWNLOADS,
                   help=f'原始记录保存目录(默认 {DEFAULT_DOWNLOADS})')
    c.add_argument('--attention', help='关注范围 JSON(inputs/request.json 格式)')
    c.add_argument('--attention-only', action='store_true',
                   help='只采集关注清单覆盖的组合,其余组合仍会被发现并记录')
    c.add_argument('--no-new-framework-discovery', action='store_true',
                   help='不自动跟进新出现的框架分支(仍会记录其存在)')
    c.add_argument('--full', action='store_true', help='忽略续采状态,全部重采')
    c.add_argument('--headed', action='store_true', help='显示浏览器窗口(调试用)')
    c.add_argument('--settle-timeout', type=int, default=15000, help='页面稳定等待毫秒数')

    q = sub.add_parser('query', help='按组合查询最新结论及出处')
    _add_db(q)
    q.add_argument('--card')
    q.add_argument('--driver')
    q.add_argument('--framework')
    q.add_argument('--precision')
    q.add_argument('--model')

    r = sub.add_parser('report', help='资料库总览:已观察/无依据/矛盾/新分支')
    _add_db(r)

    a = sub.add_parser('attention', help='关注清单逐项核对')
    _add_db(a)
    a.add_argument('--attention', help='关注范围 JSON;缺省用采集时入库的清单')

    args = parser.parse_args(argv)

    if args.cmd == 'collect':
        attention, allow_new = [], True
        if args.attention:
            attention, allow_new = load_request(args.attention)
        if args.no_new_framework_discovery:
            allow_new = False
        store = Store(args.db)
        try:
            collector = Collector(
                args.portal, store, args.downloads,
                attention=attention, attention_only=args.attention_only,
                allow_new_framework_discovery=allow_new,
                full=args.full, headed=args.headed,
                settle_timeout_ms=args.settle_timeout)
            run_id = collector.run()
            observed = store.run_observations(run_id)
            attempts = store.run_attempts(run_id)
            print(f'运行 #{run_id} 完成:新增观察 {len(observed)} 条,未完成尝试 {len(attempts)} 条')
            print()
            print(query_mod.report(store))
        finally:
            store.close()
        return 0

    store = Store(args.db)
    try:
        if args.cmd == 'query':
            filters = {k: v for k, v in (
                ('card', args.card), ('driver', args.driver),
                ('framework', args.framework), ('precision', args.precision),
                ('model', args.model)) if v}
            print(query_mod.query(store, filters))
        elif args.cmd == 'report':
            print(query_mod.report(store))
        elif args.cmd == 'attention':
            entries = None
            if args.attention:
                entries, _ = load_request(args.attention)
                entries = [e.__dict__ for e in entries]
            print(query_mod.attention_report(store, entries))
    finally:
        store.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
