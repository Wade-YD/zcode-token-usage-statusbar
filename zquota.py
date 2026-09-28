# -*- coding: utf-8 -*-
"""zquota —— 服务商额度/余额查询模块（供 zusage 状态条的额度条目使用）。

数据来源：按最后一条调用的 provider_id 三级解析服务商（~/.zcode/v2/config.json →
provider_config.json 的 providerRules → account:<family> 回落 builtin），调用其余额 /
套餐接口。支持在服务商配置里提供 balanceUrl / balanceKind 自定义端点（中转站扩展点）。

安全：仅 https；host 解析结果必须为公网地址（fake-IP 198.18.0.0/15 豁免）；拒绝跟随
重定向（防 Authorization 头跨域外带）；API Key 只读自 ~/.zcode 配置，绝不写入日志/界面。

集成契约（zusage.py 侧）：
    get_quota(provider_id=None, cfg=None) -> dict   # 永不抛出
      ok=True  : {ok, kind: quota|balance, provider_name, windows|currencies, available, fetched_at, age_s}
      ok=False : {ok, reason: unsupported|no_key|no_base|error, provider_name, message, stale?, age_s?}
    缓存 300s（config 可调 quota.ttl_s）：命中即返回；过期且有旧值时立即返回旧值并后台
    刷新（绝不占用泵的 15s 查询预算）；失败不覆盖上次成功数据（以 stale+age_s 呈现）。

调试：
    python zquota.py                      # 查询当前服务商（最后一条调用所属）
    python zquota.py --provider <id>      # 指定 provider_id 查询
    python zquota.py --selftest           # 无网络防护自检（host 白名单/公网校验/scheme）
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ZCODE_HOME = Path(os.environ.get("ZCODE_HOME") or Path.home() / ".zcode")
DB_PATH = ZCODE_HOME / "cli" / "db" / "db.sqlite"
PROVIDER_CONFIG = ZCODE_HOME / "v2" / "config.json"
PROVIDER_RULES = ZCODE_HOME / "v2" / "provider_config.json"

# RFC 2544 基准测速段：本机代理的 fake-IP DNS 常把所有域名解析到这里，
# 流量由代理转发出站，不是可达的内网目标
FAKEIP_NET = ipaddress.ip_network("198.18.0.0/15")
HOST_SUFFIXES = ("deepseek.com", "z.ai", "bigmodel.cn", "openrouter.ai")
DEFAULT_TTL_S = 300
HTTP_TIMEOUT = 5          # 必须短：常驻泵单次快照预算 15s，额度查询不得拖垮它

_cache: dict = {"key": None, "at": 0.0, "payload": None}
_cache_lock = threading.Lock()
_fetch_lock = threading.Lock()
_inflight = False
# 连续失败退避：网络类失败后 30s 内不重试，之后 ×2 封顶 300s——
# 否则每个快照周期（1.5s）都重试，密集重试可能触发服务商限流，把瞬时故障放大成长时间锁死
_fail_state: dict = {"at": 0.0, "backoff": 30.0, "error": ""}


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _reset_text(value) -> str | None:
    """nextResetTime 可能是毫秒时间戳或 ISO 字符串，统一成 MM-DD HH:MM。"""
    if value is None:
        return None
    moment = None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            moment = datetime.fromtimestamp(seconds).astimezone()
        except (OSError, OverflowError, ValueError):
            return None
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
        except ValueError:
            return str(value)
    return moment.strftime("%m-%d %H:%M")


# --------------------------------------------------------------------------
# 服务商解析（三级）
# --------------------------------------------------------------------------

def latest_provider_id() -> str | None:
    """最后一条完成调用所属的 provider_id（只读）。"""
    if not DB_PATH.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "SELECT provider_id FROM model_usage WHERE status='completed' "
            "ORDER BY started_at DESC LIMIT 1").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _provider_entry(provider_id: str | None) -> dict:
    """按 providerId 找到可查询的服务商条目（三级解析，含自定义余额端点透传）。"""
    if not provider_id:
        return {}
    providers = (_load_json(PROVIDER_CONFIG).get("provider") or {})
    entry = providers.get(provider_id)
    if entry:
        return entry

    rules = (((_load_json(PROVIDER_RULES).get("config") or {})
              .get("providerConfigRules") or {}).get("providerRules") or [])
    for rule in rules:
        if rule.get("providerId") != provider_id:
            continue
        rc = rule.get("config") or {}
        access = rc.get("access") or {}
        api = rc.get("api") or {}
        return {
            "name": rule.get("providerName") or provider_id,
            "enabled": rule.get("enabled"),
            "options": {
                "apiKey": (access.get("apiKey") or "").strip(),
                "baseURL": (api.get("baseUrl") or "").strip(),
                # 自定义余额端点（中转站扩展点）
                "balanceUrl": (rc.get("balanceUrl") or api.get("balanceUrl") or "").strip(),
                "balanceKind": (rc.get("balanceKind") or api.get("balanceKind") or "").strip(),
            },
        }

    if provider_id.startswith("account:"):
        family = provider_id.split(":", 1)[1].split("-", 1)[0]
        if family:
            for pid, item in providers.items():
                options = item.get("options") or {}
                if family in pid and item.get("enabled") and options.get("apiKey"):
                    return item
    return {}


def _match_provider(host: str) -> str | None:
    """host → 服务商家族（精确后缀匹配；禁止子串包含）。"""
    host = (host or "").lower().rstrip(".")
    for suffix in HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            if suffix == "bigmodel.cn" or suffix == "z.ai":
                return "glm"
            return "deepseek" if suffix == "deepseek.com" else "openrouter"
    return None


# --------------------------------------------------------------------------
# 防护闸：https + 公网解析 + 拒重定向
# --------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """拒绝跟随重定向：跨域跳转会带着 Authorization 头走，Key 可能被外带。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _assert_public_host(hostname: str) -> None:
    """SSRF 防护：host 解析结果必须是可出站的公网目标（fake-IP 段豁免）。"""
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"无法解析主机 {hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.version == 4 and ip in FAKEIP_NET:
            continue
        if not ip.is_global:
            raise ValueError(f"拒绝非公网地址 {ip}（host={hostname}）")


def _http_json(url: str, api_key: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"拒绝非 https 请求：{url}")
    _assert_public_host(parsed.hostname)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "zquota/1.0",
    })
    with urllib.request.build_opener(_NoRedirect).open(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


# --------------------------------------------------------------------------
# 三家解析（解析与请求分离，供自定义端点复用）
# --------------------------------------------------------------------------

def _parse_balance(data: dict, name: str, endpoint: str) -> dict:
    currencies = [{
        "currency": item.get("currency"),
        "total": item.get("total_balance"),
        "granted": item.get("granted_balance"),
        "topped_up": item.get("topped_up_balance"),
    } for item in (data.get("balance_infos") or [])]
    currencies.sort(key=lambda c: abs(_to_float(c["total"])), reverse=True)
    return {"ok": True, "kind": "balance", "provider_name": name, "endpoint": endpoint,
            "available": bool(data.get("is_available")), "currencies": currencies}


def _glm_window_label(item: dict) -> str:
    kind = (item.get("type") or "").upper()
    unit = item.get("unit")
    if kind in ("TOKENS_LIMIT", "CREDIT_LIMIT"):     # BigModel 实际返回 CREDIT_LIMIT
        if unit == 3:
            return "5 小时"
        if unit == 6:
            return "每周"
    if kind == "TIME_LIMIT":
        return "月度工具"
    return f"{kind or '额度'}" + (f"(unit={unit})" if unit is not None else "")


def _glm_window(item: dict) -> dict:
    limit = _to_float(item.get("usage"))
    used = _to_float(item.get("currentValue"))
    pct = item.get("percentage")
    remaining_pct = None
    if pct is not None:
        remaining_pct = max(0.0, 100.0 - _to_float(pct))
    elif limit > 0:
        remaining_pct = max(0.0, 100.0 * (limit - used) / limit)
    remaining = item.get("remaining")
    if remaining is None and limit > 0:
        remaining = limit - used
    return {
        "label": _glm_window_label(item),
        "remaining_pct": remaining_pct,
        "remaining": _to_float(remaining) if remaining is not None else None,
        "limit": limit if limit > 0 else None,
        "used": used if limit > 0 else None,
        "reset_at": _reset_text(item.get("nextResetTime")),
    }


def _parse_quota(data: dict, name: str, endpoint: str) -> dict:
    if data.get("success") is False or (data.get("code") not in (None, 200, "200")):
        raise ValueError(f"额度查询失败：{data.get('msg') or data.get('message') or '未知错误'}")
    limits = (data.get("data") or {}).get("limits") or []
    windows = [_glm_window(item) for item in limits if isinstance(item, dict)]
    if not windows:
        raise ValueError("没有返回额度数据（可能不是套餐账号）")
    return {"ok": True, "kind": "quota", "provider_name": name, "endpoint": endpoint,
            "available": True, "windows": windows}


def _parse_credits(data: dict, name: str, endpoint: str) -> dict:
    info = data.get("data") or {}
    total = _to_float(info.get("total_credits"))
    used = _to_float(info.get("total_usage"))
    return {"ok": True, "kind": "balance", "provider_name": name, "endpoint": endpoint,
            "available": True,
            "currencies": [{"currency": "USD", "total": f"{total - used:.2f}",
                            "topped_up": f"{total:.2f}", "granted": "0.00"}]}


# --------------------------------------------------------------------------
# 查询调度
# --------------------------------------------------------------------------

def _invalid(reason: str, name: str | None, message: str) -> dict:
    return {"ok": False, "reason": reason, "provider_name": name, "message": message}


def _fetch(provider_id: str | None) -> dict:
    """单次查询，永不抛出。"""
    try:
        provider = _provider_entry(provider_id)
        if not provider:
            return _invalid("unsupported", provider_id,
                            "未找到当前服务商配置（读不到 ~/.zcode/v2/config.json）")
        options = provider.get("options") or {}
        name = provider.get("name") or provider_id or "未知服务商"
        api_key = (options.get("apiKey") or "").strip()
        base_url = (options.get("baseURL") or "").strip()
        balance_url = (options.get("balanceUrl") or "").strip()
        balance_kind = (options.get("balanceKind") or "").strip()

        if not api_key:
            return _invalid("no_key", name, "当前服务商没有配置 API Key")

        if balance_url:                       # 自定义端点（中转站扩展点）
            data = _http_json(balance_url, api_key)
            if balance_kind == "balance":
                return _parse_balance(data, name, balance_url)
            if balance_kind == "quota":
                return _parse_quota(data, name, balance_url)
            # 未指定 kind：两种形态都试
            try:
                return _parse_quota(data, name, balance_url)
            except ValueError:
                return _parse_balance(data, name, balance_url)

        if not base_url:
            return _invalid("no_base", name, "当前服务商没有配置 baseURL")

        host = urlparse(base_url).hostname or ""
        family = _match_provider(host)
        if family == "deepseek":
            return _parse_balance(_http_json(f"https://{host}/user/balance", api_key), name,
                                  f"https://{host}/user/balance")
        if family == "glm":
            return _parse_quota(_http_json(f"https://{host}/api/monitor/usage/quota/limit", api_key),
                                name, f"https://{host}/api/monitor/usage/quota/limit")
        if family == "openrouter":
            return _parse_credits(_http_json(f"https://{host}/api/v1/credits", api_key), name,
                                  f"https://{host}/api/v1/credits")
        return _invalid("unsupported", name,
                        f"{name} 暂不支持自动查询（已支持 DeepSeek / 智谱·Z.ai / OpenRouter；"
                        f"中转站可配置 balanceUrl）")
    except Exception as exc:                  # 网络 / 鉴权 / 解析 / 防护拦截
        return _invalid("error", None, f"查询失败：{exc}")


def _age_of(key: str | None) -> int | None:
    with _cache_lock:
        if _cache["payload"] is None or _cache["key"] != key:
            return None
        return int(time.monotonic() - _cache["at"])


def _store(key: str | None, payload: dict) -> None:
    payload = dict(payload)
    payload.setdefault("fetched_at", datetime.now().astimezone().strftime("%H:%M"))
    with _cache_lock:
        _cache.update(key=key, at=time.monotonic(), payload=payload)


def _fail_gate_ok() -> bool:
    """失败退避闸：距上次网络类失败不足 backoff 秒时不发起新的刷新（需持有 _cache_lock）。"""
    return not (_fail_state["at"] and time.monotonic() - _fail_state["at"] < _fail_state["backoff"])


def _maybe_schedule_refresh(key: str | None) -> bool:
    """过期/缺缓存时安排一次后台刷新（单飞 + 失败退避闸）。需持有 _cache_lock。"""
    global _inflight
    if _inflight or not _fail_gate_ok():
        return False
    _inflight = True
    threading.Thread(target=_background_refresh, args=(key,), daemon=True).start()
    return True


def _background_refresh(key: str | None) -> None:
    global _inflight
    try:
        payload = _fetch(key)
        if payload.get("ok"):
            _store(key, payload)
            with _cache_lock:
                _fail_state.update(at=0.0, backoff=30.0, error="")
        elif payload.get("reason") == "error":
            # 网络类失败：留痕（供 UI 显示「上次刷新失败原因」）并按退避推迟下次重试
            with _cache_lock:
                _fail_state["at"] = time.monotonic()
                _fail_state["error"] = str(payload.get("message") or "")[:160]
                _fail_state["backoff"] = min(_fail_state["backoff"] * 2, 300.0)
    finally:
        with _cache_lock:
            _inflight = False


def get_quota(provider_id: str | None = None, cfg: dict | None = None) -> dict:
    """额度快照，永不抛出。语义见模块 docstring。"""
    if ((cfg or {}).get("quota") or {}).get("enabled") is False:
        return {"ok": False, "reason": "disabled", "provider_name": None,
                "message": "额度查询已关闭（quota.enabled=false）", "age_s": None}
    pid = provider_id or latest_provider_id()
    ttl = max(30, int(((cfg or {}).get("quota") or {}).get("ttl_s") or DEFAULT_TTL_S))
    now = time.monotonic()

    with _cache_lock:
        hit = _cache["payload"] is not None and _cache["key"] == pid
        age = int(now - _cache["at"]) if hit else None
        if hit and age < ttl:
            payload = dict(_cache["payload"])
            payload["age_s"] = 0
            payload["stale"] = False
            return payload
        stale = dict(_cache["payload"]) if hit else None

    if stale is not None:
        # 过期：立即返回旧值，后台刷新（单飞 + 失败退避闸，不占泵的快照预算）
        with _cache_lock:
            _maybe_schedule_refresh(pid)
        stale["age_s"] = age
        stale["stale"] = True
        stale["last_error"] = _fail_state["error"] or None
        return stale

    # 首次（无缓存）：同步取一次，短超时兜底
    with _fetch_lock:
        fresh = _fetch(pid)
    if fresh.get("ok"):
        _store(pid, fresh)
        fresh["age_s"] = 0
        fresh["stale"] = False
        return fresh
    fresh["age_s"] = None
    return fresh


def cached_payload(provider_id: str | None = None, cfg: dict | None = None) -> dict:
    """只读缓存视图（不触发任何网络）：供快照高频路径使用。

    有缓存（无论新旧）→ 返回缓存数据 + age_s；无缓存且未在后台刷新 → 触发一次
    后台预热并返回 {ok:False, reason:'warming', ...}。
    config quota.enabled=false 时返回 disabled，不触网、不起线程。
    """
    if ((cfg or {}).get("quota") or {}).get("enabled") is False:
        return {"ok": False, "reason": "disabled", "provider_name": None,
                "message": "额度查询已关闭（quota.enabled=false）", "age_s": None}
    global _inflight
    pid = provider_id or latest_provider_id()
    ttl = max(30, int(((cfg or {}).get("quota") or {}).get("ttl_s") or DEFAULT_TTL_S))
    with _cache_lock:
        hit = _cache["payload"] is not None and _cache["key"] == pid
        if hit:
            payload = dict(_cache["payload"])
            age = int(time.monotonic() - _cache["at"])
            payload["age_s"] = age
            payload["stale"] = age >= ttl
            if age >= ttl:
                payload["last_error"] = _fail_state["error"] or None
                _maybe_schedule_refresh(pid)
            return payload
        started = _maybe_schedule_refresh(pid)
    if started:
        return {"ok": False, "reason": "warming", "provider_name": None, "message": "额度数据预热中…",
                "age_s": None}
    return {"ok": False, "reason": "error", "provider_name": None,
            "message": _fail_state["error"] or "上次刷新失败，退避后自动重试", "age_s": None}


# --------------------------------------------------------------------------
# 自检与 CLI
# --------------------------------------------------------------------------

def _selftest() -> int:
    failures = []

    def check(name, cond):
        print(("  PASS  " if cond else "  FAIL  ") + name)
        if not cond:
            failures.append(name)

    print("zquota selftest（无网络）")
    check("子串仿冒不命中：evil-deepseek.example",
          _match_provider("evil-deepseek.example") is None)
    check("后缀命中：api.deepseek.com", _match_provider("api.deepseek.com") == "deepseek")
    check("后缀命中：open.bigmodel.cn", _match_provider("open.bigmodel.cn") == "glm")
    check("后缀命中：api.z.ai", _match_provider("api.z.ai") == "glm")
    check("后缀命中：openrouter.ai", _match_provider("openrouter.ai") == "openrouter")
    check("非 https 被拒", _rejects(lambda: _http_json("http://example.com/x", "k")))
    check("内网地址被拒", _rejects(lambda: _assert_public_host("192.168.1.10")))
    check("环回地址被拒", _rejects(lambda: _assert_public_host("127.0.0.1")))
    check("localhost 被拒", _rejects(lambda: _assert_public_host("localhost")))
    check("fake-IP 段豁免逻辑", ipaddress.ip_address("198.18.0.153") in FAKEIP_NET)
    check("重定向处理器拒绝跳转",
          _NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example") is None)
    print("结果：", "全部通过" if not failures else f"{len(failures)} 项失败：{failures}")
    return 1 if failures else 0


def _rejects(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True
    except Exception:
        return False


def main(argv: list[str]) -> int:
    provider = None
    force = False
    if "--selftest" in argv:
        return _selftest()
    if "--provider" in argv:
        provider = argv[argv.index("--provider") + 1] if len(argv) > argv.index("--provider") + 1 else None
    if "--force" in argv:
        force = True
    payload = get_quota(provider) if force else cached_payload(provider)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
