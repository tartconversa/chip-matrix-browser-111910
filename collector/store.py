"""本地资料库:SQLite 持久化,重启后可续采。

五类事实分开存:
- runs:         每次采集运行;
- combinations: 页面实际出现过的可选组合(发现的分支),含首次/最近见到的运行;
- revisions:    每卡型观察到的适配修订史,用于发现发布方更正;
- observations: 一致采集到的组合证据(页面选择、组合标识、修订三者一致),
                汇总状态与子项矛盾时双方原文都保留;
- attempts:     未完成的选择过程及页面原文证据,绝不记成"不支持";
- attention:    同事给的关注组合范围。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  portal TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS combinations (
  card TEXT NOT NULL,
  driver TEXT NOT NULL,
  framework TEXT NOT NULL,
  precision TEXT NOT NULL,
  selectable INTEGER NOT NULL DEFAULT 1,
  first_seen_run INTEGER NOT NULL,
  last_seen_run INTEGER NOT NULL,
  PRIMARY KEY (card, driver, framework, precision)
);
CREATE TABLE IF NOT EXISTS revisions (
  card TEXT NOT NULL,
  revision INTEGER NOT NULL,
  first_seen_run INTEGER NOT NULL,
  first_seen_at TEXT NOT NULL,
  PRIMARY KEY (card, revision)
);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  collected_at TEXT NOT NULL,
  card TEXT NOT NULL,
  driver TEXT NOT NULL,
  framework TEXT NOT NULL,
  precision TEXT NOT NULL,
  identity TEXT NOT NULL,
  revision INTEGER NOT NULL,
  summary_status TEXT NOT NULL,
  detail_status TEXT NOT NULL,
  build TEXT NOT NULL,
  model TEXT NOT NULL,
  operator_name TEXT NOT NULL,
  operator_status TEXT NOT NULL,
  conditions TEXT NOT NULL,
  contradiction INTEGER NOT NULL,
  options_json TEXT NOT NULL,
  download_path TEXT,
  sha256 TEXT,
  download_revision INTEGER,
  consistent INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  at TEXT NOT NULL,
  card TEXT, driver TEXT, framework TEXT, precision TEXT,
  outcome TEXT NOT NULL,
  evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attention (
  card TEXT NOT NULL,
  model TEXT NOT NULL,
  framework TEXT NOT NULL,
  precision TEXT NOT NULL,
  PRIMARY KEY (card, model, framework, precision)
);
"""

OBSERVATION_COLUMNS = (
    'run_id', 'collected_at', 'card', 'driver', 'framework', 'precision',
    'identity', 'revision', 'summary_status', 'detail_status', 'build',
    'model', 'operator_name', 'operator_status', 'conditions',
    'contradiction', 'options_json', 'download_path', 'sha256',
    'download_revision', 'consistent',
)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class Store:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ':memory:':
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # ---- 运行 ----

    def start_run(self, portal, note=''):
        cur = self.db.execute(
            'INSERT INTO runs (started_at, portal, note) VALUES (?,?,?)',
            (now(), portal, note))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id):
        self.db.execute('UPDATE runs SET finished_at=? WHERE id=?', (now(), run_id))
        self.db.commit()

    def run_list(self):
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM runs ORDER BY id').fetchall()]

    # ---- 组合空间与修订 ----

    def see_combination(self, combo, run_id):
        self.db.execute(
            '''INSERT INTO combinations
                 (card, driver, framework, precision, selectable, first_seen_run, last_seen_run)
               VALUES (?,?,?,?,1,?,?)
               ON CONFLICT (card, driver, framework, precision)
               DO UPDATE SET selectable=1, last_seen_run=excluded.last_seen_run''',
            (combo.card, combo.driver, combo.framework, combo.precision, run_id, run_id))
        self.db.commit()

    def mark_unseen_not_selectable(self, run_id):
        """本次运行没再出现的组合标记为当前不可选(历史观察保留)。"""
        self.db.execute(
            'UPDATE combinations SET selectable=0 WHERE last_seen_run != ?', (run_id,))
        self.db.commit()

    def all_combinations(self):
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM combinations ORDER BY card, driver, framework, precision').fetchall()]

    def record_revision_if_new(self, card, revision, run_id):
        cur = self.db.execute(
            'INSERT OR IGNORE INTO revisions (card, revision, first_seen_run, first_seen_at)'
            ' VALUES (?,?,?,?)',
            (card, revision, run_id, now()))
        self.db.commit()
        return cur.rowcount > 0

    def max_revision(self, card):
        row = self.db.execute(
            'SELECT MAX(revision) AS m FROM revisions WHERE card=?', (card,)).fetchone()
        return row['m']

    def revisions(self, card):
        return [r['revision'] for r in self.db.execute(
            'SELECT revision FROM revisions WHERE card=? ORDER BY revision', (card,)).fetchall()]

    def cards(self):
        return [r['card'] for r in self.db.execute(
            'SELECT DISTINCT card FROM combinations ORDER BY card').fetchall()]

    def frameworks_first_seen_in(self, run_id):
        return {r['framework'] for r in self.db.execute(
            'SELECT DISTINCT framework FROM combinations WHERE first_seen_run=?',
            (run_id,)).fetchall()}

    # ---- 观察与尝试 ----

    def record_observation(self, **fields):
        cols = [c for c in OBSERVATION_COLUMNS if c in fields]
        cur = self.db.execute(
            f"INSERT INTO observations ({', '.join(cols)})"
            f" VALUES ({', '.join('?' for _ in cols)})",
            [fields[c] for c in cols])
        self.db.commit()
        return cur.lastrowid

    def record_attempt(self, run_id, combo, outcome, evidence):
        self.db.execute(
            'INSERT INTO attempts (run_id, at, card, driver, framework, precision, outcome, evidence)'
            ' VALUES (?,?,?,?,?,?,?,?)',
            (run_id, now(), combo.card, combo.driver, combo.framework,
             combo.precision, outcome, evidence))
        self.db.commit()

    def all_observations(self):
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM observations ORDER BY id').fetchall()]

    def all_attempts(self):
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM attempts ORDER BY id').fetchall()]

    def run_observations(self, run_id):
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM observations WHERE run_id=? ORDER BY id', (run_id,)).fetchall()]

    def run_attempts(self, run_id):
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM attempts WHERE run_id=? ORDER BY id', (run_id,)).fetchall()]

    def latest_observation(self, combo):
        row = self.db.execute(
            '''SELECT * FROM observations
               WHERE card=? AND driver=? AND framework=? AND precision=?
               ORDER BY id DESC LIMIT 1''',
            (combo.card, combo.driver, combo.framework, combo.precision)).fetchone()
        return dict(row) if row else None

    _FILTER_COLUMNS = {'card', 'driver', 'framework', 'precision', 'model'}

    def latest_observations(self, **filters):
        unknown = set(filters) - self._FILTER_COLUMNS
        if unknown:
            raise ValueError(f'不支持的筛选字段: {sorted(unknown)}')
        where = ' AND '.join(f'{k}=?' for k in filters)
        sql = '''SELECT o.* FROM observations o
                 JOIN (SELECT card, driver, framework, precision, MAX(id) AS mid
                       FROM observations'''
        if where:
            sql += f' WHERE {where}'
        sql += ''' GROUP BY card, driver, framework, precision) t
                 ON o.id = t.mid
                 ORDER BY o.card, o.driver, o.framework, o.precision'''
        return [dict(r) for r in self.db.execute(sql, list(filters.values())).fetchall()]

    def combinations_matching(self, card, framework, precision, selectable_only=True):
        sql = ('SELECT * FROM combinations WHERE card=? AND framework=? AND precision=?')
        params = [card, framework, precision]
        if selectable_only:
            sql += ' AND selectable=1'
        return [dict(r) for r in self.db.execute(sql + ' ORDER BY driver', params).fetchall()]

    # ---- 关注清单 ----

    def set_attention(self, entries):
        self.db.execute('DELETE FROM attention')
        self.db.executemany(
            'INSERT OR IGNORE INTO attention (card, model, framework, precision) VALUES (?,?,?,?)',
            [(e.card, e.model, e.framework, e.precision) for e in entries])
        self.db.commit()

    def attention(self):
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM attention ORDER BY card, framework, precision, model').fetchall()]

    def attention_json(self):
        return json.dumps(self.attention(), ensure_ascii=False)
