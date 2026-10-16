# -*- coding: utf-8 -*-
"""生成速度窗口口径回归（add-plugin-shell cherry-pick：未上报思考的模型按含思考窗口计时）。

数据与上游真库裁决同款：输出 1074 tok、TTFT 13661ms、总耗时 15087ms——
首字窗口（completed − first_token = 1426ms）= 753.2 tok/s，Claude 系列属虚高口径；
含思考窗口（completed − started = 15087ms）= 71.2 tok/s。

运行：python -m unittest discover -s tests
"""
import sqlite3
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import zusage  # noqa: E402

START = 1_000_000
TTFT_MS = 13661
TOTAL_MS = 15087
OUT = 1074
DECODE_S = (TOTAL_MS - TTFT_MS) / 1000.0     # 1.426s 首字窗口
THINK_S = TOTAL_MS / 1000.0                  # 15.087s 含思考窗口


def row(out=OUT, rea=0, dur=TOTAL_MS, fta=START + TTFT_MS, ca=START + TOTAL_MS,
        sa=START, model="test-model"):
    return (out, rea, dur, fta, ca, sa, model)


class RateOfWindowTests(unittest.TestCase):
    def test_claude_window_shifts_to_started(self):
        # Claude 系列 reasoning 恒 0 但 output 已含思考、思考在首字之前流出：
        # 生成速度须按含思考窗口（completed − started），不再按首字窗口虚高。
        tps, vis, basis = zusage.rate_of(row(model="claude-opus-5-5"))
        self.assertEqual(tps, round(OUT / THINK_S, 1))
        self.assertIsNone(vis)          # 未上报思考拆分 → 无可见速度
        self.assertEqual(basis, "decode")

    def test_reasoning_reported_also_shifts(self):
        # 含思考段（reasoning>0）：生成速度窗口前移；可见速度保持首字窗口口径。
        rea = 400
        tps, vis, _ = zusage.rate_of(row(rea=rea, model="deepseek-chat"))
        self.assertEqual(tps, round(OUT / THINK_S, 1))
        self.assertEqual(vis, round((OUT - rea) / DECODE_S, 1))

    def test_plain_model_unchanged(self):
        # 普通模型（非 Claude、无思考）：口径与历史完全一致（首字窗口）。
        tps, vis, basis = zusage.rate_of(row())
        self.assertEqual(tps, round(OUT / DECODE_S, 1))
        self.assertIsNone(vis)
        self.assertEqual(basis, "decode")

    def test_claude_without_started_at_degrades_gracefully(self):
        # started_at 缺失（0/None）：不前移，退回首字窗口，不崩不出负值。
        for sa in (0, None):
            tps, vis, basis = zusage.rate_of(row(sa=sa, model="claude-opus-5-5"))
            self.assertEqual(tps, round(OUT / DECODE_S, 1))
            self.assertEqual(basis, "decode")

    def test_hides_reasoning_scope(self):
        self.assertTrue(zusage.hides_reasoning("claude-opus-5-5"))
        self.assertTrue(zusage.hides_reasoning("Claude-Sonnet"))
        self.assertFalse(zusage.hides_reasoning("glm-5.3-flash"))
        self.assertFalse(zusage.hides_reasoning(None))


class SpeedStatsAggregationTests(unittest.TestCase):
    """speed_stats 查询列扩展（started_at/model_id）后聚合口径不回归。"""

    SCHEMA = """
    create table model_usage (id text primary key, session_id text, turn_id text,
      query_source text, agent text, model_id text, status text, started_at integer,
      first_token_at integer, completed_at integer, duration_ms integer,
      time_to_first_token_ms integer, input_tokens integer, output_tokens integer,
      reasoning_tokens integer);
    """

    @staticmethod
    def _db(rows):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(SpeedStatsAggregationTests.SCHEMA)
        for i, r in enumerate(rows):
            con.execute(
                "insert into model_usage values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"r{i}", "s", f"t{i}", "main_turn", "", r["model"], "completed",
                 START, START + TTFT_MS, START + TOTAL_MS, TOTAL_MS, TTFT_MS,
                 10, r["out"], r["rea"]))
        return con

    def test_mixed_pool_uses_started_window_for_thinking_rows(self):
        con = self._db([
            dict(model="claude-opus-5-5", out=OUT, rea=0),
            dict(model="test-model", out=OUT, rea=0),
        ])
        st = zusage.speed_stats(con, window=10)
        self.assertEqual(st["samples"], 2)
        self.assertEqual(st["basis"], "decode")
        self.assertEqual(st["coverage"], 1.0)
        # claude（含思考窗口 71.2）+ 普通（首字窗口 753.2）→ 中位取均值
        self.assertEqual(st["median_tps"],
                         round((round(OUT / THINK_S, 1) + round(OUT / DECODE_S, 1)) / 2, 1))

    def test_session_scoped_query(self):
        con = self._db([dict(model="claude-opus-5-5", out=OUT, rea=0)])
        st = zusage.speed_stats(con, sid="s", window=10)
        self.assertEqual(st["median_tps"], round(OUT / THINK_S, 1))


if __name__ == "__main__":
    unittest.main()
