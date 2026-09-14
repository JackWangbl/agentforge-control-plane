from __future__ import annotations

import ipaddress
import os
import socket
import threading
from typing import Any, Optional
from urllib.parse import urlparse

_lock = threading.Lock()
_playwright = None
_browser = None
_contexts: dict[str, Any] = {}
_pages: dict[str, Any] = {}

ALLOWED_SCHEMES = {"http", "https"}
MAX_TEXT = 8000


def browser_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "browser_open",
            "description": "用可交互浏览器打开网页并返回标题、地址和可见正文。1688、需要登录或动态加载的页面必须用这个工具，不要空口说打不开。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整 http/https 链接"},
                    "wait_ms": {"type": "integer", "description": "额外等待毫秒，默认 2500"},
                },
                "required": ["url"],
            },
        },
        {
            "name": "browser_text",
            "description": "读取当前浏览器页面的可见文本。打开页面后继续摘字段时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_chars": {"type": "integer", "description": "最多返回多少字，默认 8000"},
                },
            },
        },
        {
            "name": "browser_links",
            "description": "列出当前页面链接。可按关键词过滤，例如 offer、头巾、hijab。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "只保留包含该关键词的链接或锚文本"},
                    "limit": {"type": "integer", "description": "最多返回多少条，默认 20"},
                },
            },
        },
        {
            "name": "browser_close",
            "description": "关闭当前浏览器会话，释放资源。",
            "parameters": {"type": "object", "properties": {}},
        },
    ]


def execute_browser_tool(name: str, arguments: dict[str, Any], scope_id: str = "standalone") -> str:
    try:
        if name == "browser_open":
            return open_page(str(arguments.get("url") or ""), int(arguments.get("wait_ms") or 2500), scope_id)
        if name == "browser_text":
            return page_text(int(arguments.get("max_chars") or MAX_TEXT), scope_id)
        if name == "browser_links":
            return page_links(str(arguments.get("query") or ""), int(arguments.get("limit") or 20), scope_id)
        if name == "browser_close":
            close_browser(scope_id)
            return "浏览器已关闭。"
        return f"未知浏览器工具 {name}"
    except Exception as exc:
        return f"浏览器工具失败：{exc}"


def _safe_url(url: str) -> str:
    target = (url or "").strip()
    parsed = urlparse(target)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError("请提供完整的 http/https 链接")
    if parsed.username or parsed.password:
        raise ValueError("浏览器链接不能包含用户名或密码")
    if os.getenv("BROWSER_ALLOW_PRIVATE_NETWORK", "").strip().lower() not in {
        "1", "true", "yes",
    }:
        _reject_private_host(parsed.hostname or "")
    return target


def _reject_private_host(hostname: str) -> None:
    normalized = hostname.strip().rstrip(".").lower()
    if not normalized or normalized == "localhost" or normalized.endswith(".localhost"):
        raise ValueError("浏览器不允许访问本机或私有网络地址")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(normalized, None)}
    except socket.gaierror as exc:
        raise ValueError("浏览器目标域名无法解析") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("浏览器不允许访问本机或私有网络地址")


def _ensure_page(scope_id: str):
    global _playwright, _browser
    if scope_id in _pages:
        return _pages[scope_id]
    if _browser is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("未安装 Playwright。请在控制台目录执行：.venv/bin/pip install playwright && .venv/bin/playwright install chromium") from exc
        headed = os.getenv("BROWSER_HEADED", "").strip() in {"1", "true", "yes"}
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=not headed)
    context = _browser.new_context()
    page = context.new_page()
    page.set_default_timeout(25000)
    _contexts[scope_id] = context
    _pages[scope_id] = page
    return page


def open_page(url: str, wait_ms: int = 2500, scope_id: str = "standalone") -> str:
    target = _safe_url(url)
    wait_ms = max(0, min(wait_ms, 15000))
    with _lock:
        page = _ensure_page(scope_id)
        page.goto(target, wait_until="domcontentloaded")
        if wait_ms:
            page.wait_for_timeout(wait_ms)
        title = page.title() or ""
        current = page.url
        text = _visible_text(page, MAX_TEXT)
        blocked = _block_hint(title, text, current)
        parts = [f"已打开：{title}", f"地址：{current}", text]
        if blocked:
            parts.append(blocked)
        return "\n".join(part for part in parts if part)


def page_text(max_chars: int = MAX_TEXT, scope_id: str = "standalone") -> str:
    with _lock:
        page = _pages.get(scope_id)
        if page is None:
            return "还没有打开页面，请先调用 browser_open。"
        return _visible_text(page, max(200, min(max_chars, 20000)))


def page_links(query: str = "", limit: int = 20, scope_id: str = "standalone") -> str:
    limit = max(1, min(limit, 50))
    with _lock:
        page = _pages.get(scope_id)
        if page is None:
            return "还没有打开页面，请先调用 browser_open。"
        items = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(el => ({href: el.href || '', text: (el.innerText || '').trim()}))",
        )
    needle = (query or "").strip().lower()
    rows = []
    seen = set()
    for item in items or []:
        href = str(item.get("href") or "").strip()
        text = " ".join(str(item.get("text") or "").split())
        if not href.startswith("http"):
            continue
        blob = f"{href} {text}".lower()
        if needle and needle not in blob:
            continue
        key = href.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        rows.append(f"- {text or href} | {href}")
        if len(rows) >= limit:
            break
    if not rows:
        return "当前页没有匹配的链接。"
    return "页面链接：\n" + "\n".join(rows)


def close_browser(scope_id: Optional[str] = None) -> None:
    global _playwright, _browser
    with _lock:
        scope_ids = [scope_id] if scope_id is not None else list(_pages)
        for key in scope_ids:
            page = _pages.pop(key, None)
            context = _contexts.pop(key, None)
            try:
                if page is not None:
                    page.close()
            except Exception:
                pass
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
        if scope_id is None and _browser is not None:
            try:
                _browser.close()
            except Exception:
                pass
        if scope_id is None and _playwright is not None:
            try:
                _playwright.stop()
            except Exception:
                pass
        if scope_id is None:
            _playwright = _browser = None


def _visible_text(page, max_chars: int) -> str:
    text = page.evaluate("() => (document.body && document.body.innerText) || ''")
    compact = "\n".join(line.strip() for line in str(text).splitlines() if line.strip())
    if len(compact) > max_chars:
        compact = compact[:max_chars] + "\n…（正文已截断）"
    return compact or "页面可见文本为空（可能是验证码、登录墙或纯脚本渲染）。"


def _block_hint(title: str, text: str, url: str) -> str:
    blob = f"{title}\n{text}\n{url}".lower()
    if any(token in blob for token in ("验证码", "captcha", "滑动验证", "punish", "login.1688", "请登录", "扫码登录")):
        return "提示：页面疑似验证码或登录墙。可设置环境变量 BROWSER_HEADED=1 后重启控制台，用有界面浏览器手动通过验证，再继续调用 browser_text / browser_links。"
    return ""
