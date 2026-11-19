# -*- coding: utf-8 -*-
"""会话累计口径回归：主会话 + 全部后代会话（子代理 / 侧聊子会话 / 孙代，parent_id 递归）。

背景（v1.4.3 修复）：数据层完整包含子代理记录（session.parent_id 指向主会话），但聚合
口径只按单 session_id 取数——"当前会话累计"不含子代理消耗；且侧聊子会话（"Selection
side chat"，无 sess_subagent 前缀、query_source=main_turn）被 query_source='subagent'
过滤漏掉。修复后：session_usage 递归合并全部后代；last_request_input 钉死主会话；
turns 按 session_id||':'||turn_id 去重；sub 明细块只按 parent_id 归并。

运行：python -m unittest discover -s tests
"""
import sqlite3
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import zusage  # noqa: E402

# 时间基线：now-60s 起，逐请求 +1s——默认都落在"最近 30 分钟"内，活跃判定可预期
BASE = int(time.time() * 1000) - 60_000

# 被测 SQL 触及的最小列集：session / model_usage / part / turn_usage / tool_usage
SCHEMA = """
create table session (id text primary key, title text, directory text,
  parent_id text, time_created integer,
  summary_additions integer, summary_deletions integer, summary_files integer);
create table model_usage (id text primary key, session_id text, turn_id text,
  query_source text, agent text, model_id text, status text, started_at integer,
  first_token_at integer, completed_at integer, duration_ms integer,
  time_to_first_token_ms integer, input_tokens integer, output_tokens integer,
  reasoning_tokens integer, cache_read_input_tokens integer,
  cache_creation_input_tokens integer, computed_total_tokens integer,
  tool_call_count integer, retry_count integer, context_exceeded integer);
create table part (session_id text, data text, time_created integer, time_updated integer);
create table turn_usage (session_id text, turn_id text, status text,
  model_request_count integer, model_retry_count integer, tool_call_count integer,
  tool_error_count integer, input_tokens integer, output_tokens integer,
  reasoning_tokens integer, cache_read_input_tokens integer,
  cache_creation_input_tokens integer, computed_total_tokens integer,
  duration_ms integer, time_to_first_token_ms integer);
create table tool_usage (session_id text, tool_name text, status text, duration_ms integer);
"""

CFG = {"context_window": 128000, "poll_ms": 2000}


class MockDB:
    """内存 SQLite 造数器（模式照抄 tests/test_speed_window.py：直传 con，不碰真实 ~/.zcode）。"""

    def __init__(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.n = 0

    def session(self, sid, parent=None, title=""):
        self.con.execute(
            "insert into session values (?,?,?,?,?,NULL,NULL,NULL)",
            (sid, title, "E:/proj", parent, BASE))
        return self

    def req(self, sid, turn="t1", at=None, status="completed", qs="main_turn",
            inp=0, out=0, rea=0, crd=0, cwr=0, tools=0, retry=0, model="test-model"):
        self.n += 1
        ca = at if at is not None else BASE + self.n * 1000
        self.con.execute(
            "insert into model_usage values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"r{self.n}", sid, turn, qs, "", model, status, ca - 800, ca - 300, ca,
             1000, 300, inp, out, rea, crd, cwr, inp + out, tools, retry, 0))
        return self


class SessionUsageMergeTests(unittest.TestCase):
    """session_usage：主 + 子代理 + 侧聊子会话 + 孙代 全部合并；非 completed 不计。"""

    def setUp(self):
        zusage._AGG_CACHE.clear()
        zusage._LANG = "zh"   # 输出文案断言不随用户 config.json 的语言漂移

    def test_merges_main_subagent_sidechat_grandchild(self):
        db = MockDB()
        db.session("m1", title="main")
        db.session("sub1", parent="m1", title="sub")       # 常规子代理
        db.session("side1", parent="m1", title="side")     # 侧聊子会话：无前缀、main_turn
        db.session("g1", parent="side1", title="grand")    # 孙会话（parent 指向子）
        db.req("m1", turn="t1", inp=100, out=10)
        db.req("m1", turn="t2", inp=200, out=20)
        db.req("sub1", qs="subagent", inp=1000, out=100)
        db.req("side1", qs="main_turn", inp=500, out=50)
        db.req("g1", qs="main_turn", inp=250, out=25)
        db.req("sub1", qs="subagent", status="error", inp=99999, out=999)   # 不计
        u = zusage.session_usage(db.con, "m1")
        self.assertEqual(u["requests"], 5)
        self.assertEqual(u["input"], 100 + 200 + 1000 + 500 + 250)
        self.assertEqual(u["output"], 10 + 20 + 100 + 50 + 25)
        self.assertEqual(u["total"], 2255)

    def test_last_request_input_pinned_to_main(self):
        db = MockDB()
        db.session("m1")
        db.session("sub1", parent="m1")
        db.req("m1", inp=100, at=BASE + 1000)
        db.req("m1", inp=500, at=BASE + 2000)
        db.req("sub1", inp=99999, at=BASE + 9000)   # 后代最近请求不得污染主会话容量
        u = zusage.session_usage(db.con, "m1")
        self.assertEqual(u["last_request_input"], 500)

    def test_turns_not_collapsed_across_sessions(self):
        db = MockDB()
        db.session("m1")
        db.session("sub1", parent="m1")
        db.req("m1", turn="t1", inp=1)
        db.req("m1", turn="t1", inp=1)      # 主会话同轮两次请求 → 1 轮
        db.req("sub1", turn="t1", inp=1)    # 子会话同编号轮 → 不得与主会话塌成一轮
        u = zusage.session_usage(db.con, "m1")
        self.assertEqual(u["turns"], 2)

    def test_sid_without_session_row_still_counts_own_rows(self):
        # CTE 查不到 session 行时不得报错、不得吞掉该 sid 自己的用量
        db = MockDB()
        db.req("orphan-sid", inp=42, out=1)
        u = zusage.session_usage(db.con, "orphan-sid")
        self.assertEqual(u["requests"], 1)
        self.assertEqual(u["total"], 43)


class ResolveRootSidTests(unittest.TestCase):
    def setUp(self):
        zusage._AGG_CACHE.clear()

    def test_walks_to_root(self):
        db = MockDB()
        db.session("root")
        db.session("mid", parent="root")
        db.session("leaf", parent="mid")
        self.assertEqual(zusage._resolve_root_sid(db.con, "leaf"), "root")
        self.assertEqual(zusage._resolve_root_sid(db.con, "mid"), "root")
        self.assertEqual(zusage._resolve_root_sid(db.con, "root"), "root")

    def test_unknown_sid_returned_as_is(self):
        db = MockDB()
        db.session("root")
        self.assertEqual(zusage._resolve_root_sid(db.con, "ghost"), "ghost")

    def test_cycle_terminates_within_depth_guard(self):
        db = MockDB()
        db.session("a", parent="b")
        db.session("b", parent="a")
        r = zusage._resolve_root_sid(db.con, "a")   # 深度保护：有限步返回，不挂死
        self.assertIn(r, ("a", "b"))

    def test_descendant_ids_recursive(self):
        db = MockDB()
        db.session("m1")
        db.session("s1", parent="m1")
        db.session("s2", parent="m1")
        db.session("g1", parent="s1")
        self.assertEqual(sorted(zusage._descendant_ids(db.con, "m1")), ["g1", "s1", "s2"])
        self.assertEqual(zusage._descendant_ids(db.con, "s2"), [])


class RenderCurrentTests(unittest.TestCase):
    def setUp(self):
        zusage._AGG_CACHE.clear()
        zusage._LANG = "zh"

    def test_render_current_resolves_subagent_to_root(self):
        # 最近活跃是子代理会话：当前会话显示根主会话，合计行带"其中子代理"小计
        db = MockDB()
        db.session("m1", title="ROOT-MAIN-TITLE")
        db.session("sub1", parent="m1", title="SUB-AGENT-TITLE")
        now = int(time.time() * 1000)
        db.req("m1", inp=100, out=10, at=now - 600_000)
        db.req("sub1", qs="subagent", inp=700_000, out=7000, at=now - 1000)
        out = zusage.render_current(db.con)
        self.assertIn("ROOT-MAIN-TITLE", out)
        self.assertNotIn("SUB-AGENT-TITLE", out)
        self.assertIn("其中子代理 707.0K", out)   # 后代 completed 合计小计
        self.assertIn("合计 707.1K", out)          # 顶层 total = 主 + 后代


class SnapshotSubTests(unittest.TestCase):
    def setUp(self):
        zusage._AGG_CACHE.clear()
        zusage._LANG = "zh"

    def test_session_snapshot_sub_includes_side_chat(self):
        db = MockDB()
        db.session("m1", title="main")
        db.session("side1", parent="m1", title="side-chat")   # 无 subagent 前缀
        db.req("m1", inp=100, out=10)
        db.req("side1", qs="main_turn", inp=600, out=60)
        d = zusage._session_snapshot(db.con, "m1", CFG)
        self.assertEqual(d["sub"]["requests"], 1)
        self.assertEqual(d["sub"]["total"], 660)
        self.assertEqual(d["sub"]["list"][0]["sid"], "side1")
        self.assertEqual(d["total"], 770)   # 顶层已含子代理

    def test_light_snapshot_sub_totals_real(self):
        db = MockDB()
        db.session("m1")
        db.session("side1", parent="m1")
        db.req("m1", inp=100, out=10)
        db.req("side1", inp=600, out=60)
        d = zusage._light_snapshot(db.con, "m1", CFG)
        self.assertEqual(d["sub"]["requests"], 1)
        self.assertEqual(d["sub"]["total"], 660)
        self.assertEqual(d["sub"]["input"], 600)
        self.assertEqual(d["total"], 770)
        self.assertTrue(d["active"])   # 子树最近活动 → 主会话行活跃态

    def test_light_snapshot_without_children_keeps_zero_sub(self):
        db = MockDB()
        db.session("solo")
        db.req("solo", inp=100, out=10)
        d = zusage._light_snapshot(db.con, "solo", CFG)
        self.assertEqual(d["sub"]["total"], 0)
        self.assertEqual(d["sub"]["requests"], 0)
        self.assertEqual(d["total"], 110)


if __name__ == "__main__":
    unittest.main()
