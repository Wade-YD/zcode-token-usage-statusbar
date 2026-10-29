# -*- coding: utf-8 -*-
"""额度失败退避分级回归：限流尊重 Retry-After、解析/鉴权/配置走 30 分钟长退避。

不联网：HTTP 层用 monkeypatch 替换。运行：python -m unittest discover -s tests
"""
import sys
import unittest
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import zquota  # noqa: E402


class RetryAfterParsingTests(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(zquota._parse_retry_after("120"), 120.0)

    def test_invalid(self):
        self.assertEqual(zquota._parse_retry_after("soon"), 0.0)
        self.assertEqual(zquota._parse_retry_after(None), 0.0)


class ErrorClassificationTests(unittest.TestCase):
    def setUp(self):
        self._saved = (zquota._provider_entry, zquota._http_json, dict(zquota._fail_state),
                       dict(zquota._last_error_meta))
        zquota._provider_entry = lambda pid: {
            "name": "test", "options": {"apiKey": "k", "baseURL": "https://api.deepseek.com"}}

    def tearDown(self):
        (zquota._provider_entry, zquota._http_json, fail_state, meta) = self._saved
        zquota._fail_state.update(fail_state)
        zquota._last_error_meta.update(meta)

    def _raise(self, exc):
        def boom(url, key):
            raise exc
        zquota._http_json = boom
        return zquota._fetch("p")

    def test_rate_limit_reads_retry_after(self):
        payload = self._raise(urllib.error.HTTPError(
            "https://api.deepseek.com", 429, "Too Many Requests",
            {"Retry-After": "120"}, None))
        self.assertEqual(payload["reason"], "error")
        self.assertEqual(zquota._last_error_meta["kind"], "rate_limited")
        self.assertEqual(zquota._last_error_meta["retry_after_s"], 120.0)

    def test_auth_failure_classified(self):
        self._raise(urllib.error.HTTPError(
            "https://api.deepseek.com", 401, "Unauthorized", {}, None))
        self.assertEqual(zquota._last_error_meta["kind"], "auth")

    def test_parse_failure_classified(self):
        self._raise(zquota._ParseError("not json"))
        self.assertEqual(zquota._last_error_meta["kind"], "parse")

    def test_guard_rejection_classified_as_config(self):
        self._raise(ValueError("拒绝非 https 请求"))
        self.assertEqual(zquota._last_error_meta["kind"], "config")


class BackoffTests(unittest.TestCase):
    def setUp(self):
        self._saved = (zquota._fetch, dict(zquota._fail_state), dict(zquota._last_error_meta))
        zquota._fail_state.update(at=0.0, backoff=30.0, error="")

    def tearDown(self):
        (zquota._fetch, fail_state, meta) = self._saved
        zquota._fail_state.update(fail_state)
        zquota._last_error_meta.update(meta)

    def _refresh(self, kind, retry_after=0.0):
        zquota._fetch = lambda key: {"ok": False, "reason": "error", "message": "boom"}
        zquota._last_error_meta.update(kind=kind, retry_after_s=retry_after)
        zquota._background_refresh(None)
        return zquota._fail_state["backoff"]

    def test_parse_uses_long_backoff(self):
        self.assertEqual(self._refresh("parse"), float(zquota.PARSE_BACKOFF_S))

    def test_rate_limit_honours_retry_after(self):
        self.assertEqual(self._refresh("rate_limited", 120), 120.0)

    def test_rate_limit_clamps(self):
        self.assertEqual(self._refresh("rate_limited", 5), float(zquota.RATE_LIMIT_MIN_S))
        self.assertEqual(self._refresh("rate_limited", 99999), float(zquota.RATE_LIMIT_MAX_S))

    def test_network_doubles(self):
        self.assertEqual(self._refresh("network"), 60.0)


if __name__ == "__main__":
    unittest.main()
