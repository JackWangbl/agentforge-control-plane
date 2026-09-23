#!/usr/bin/env python3
"""Log in to 58 VIP with headless Chromium and persist scoped auth state.

Credentials are read from VIP58_USERNAME and VIP58_PASSWORD. The script never
prints cookie values or passwords. It does not attempt to bypass CAPTCHA, SMS,
QR-code, slider, or other interactive verification challenges.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Locator, Page, Playwright


DEFAULT_LOGIN_URL = (
    "https://vip.58.com/vcenter/myinfo/"
    "?PGTID=0d100000-0000-1953-9e65-002f95701e58&ClickID=6"
)
DEFAULT_STATE_FILE = Path(".auth/vip58-state.json")
DEFAULT_COOKIE_FILE = Path(".auth/vip58-cookies.json")
VERIFICATION_MARKERS = (
    "验证码",
    "滑块",
    "短信验证",
    "扫码登录",
    "安全验证",
    "异常登录",
)


def parse_args() -> argparse.Namespace:
    """Parse command-line options without accepting credentials as arguments."""
    parser = argparse.ArgumentParser(
        description="Log in to vip.58.com and save scoped authentication state.",
    )
    parser.add_argument("--login-url", default=DEFAULT_LOGIN_URL)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
    parser.add_argument(
        "--api-url",
        help="Optional HTTPS 58.com API URL to request after login.",
    )
    parser.add_argument(
        "--api-output",
        type=Path,
        help="Optional file for the API response. Defaults to stdout.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show Chromium. The default is headless mode.",
    )
    parser.add_argument("--timeout-ms", type=int, default=20_000)
    return parser.parse_args()


def validate_api_url(api_url: str | None) -> None:
    """Restrict optional authenticated requests to HTTPS endpoints under 58.com."""
    if api_url is None:
        return

    if not re.match(r"^https://([a-z0-9-]+\.)*58\.com(?:/|$)", api_url, re.IGNORECASE):
        raise ValueError("--api-url must be an HTTPS URL under the 58.com domain")


def first_visible(page: Page, selectors: Iterable[str]) -> Locator | None:
    """Return the first visible element matched by the ordered selectors."""
    for selector in selectors:
        matches = page.locator(selector)
        for index in range(matches.count()):
            candidate = matches.nth(index)
            if candidate.is_visible():
                return candidate
    return None


def select_password_login(page: Page) -> None:
    """Switch from QR login to password login when such a control is visible."""
    for label in ("账号密码登录", "密码登录", "账号登录"):
        candidates = page.get_by_text(label, exact=True)
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            if candidate.is_visible():
                candidate.click()
                return


def fill_login_form(page: Page, username: str, password: str) -> None:
    """Fill the visible username and password fields and submit the login form."""
    select_password_login(page)

    username_input = first_visible(
        page,
        (
            'input[name="username"]',
            'input[name="userName"]',
            'input[name="loginName"]',
            'input[autocomplete="username"]',
            'input[type="text"]',
            'input[type="tel"]',
        ),
    )
    password_input = first_visible(
        page,
        (
            'input[name="password"]',
            'input[autocomplete="current-password"]',
            'input[type="password"]',
        ),
    )

    if username_input is None or password_input is None:
        raise RuntimeError(
            "Could not find visible username/password inputs. "
            "The login page may have changed or may require QR login."
        )

    username_input.fill(username)
    password_input.fill(password)

    submit = first_visible(
        page,
        (
            'button:has-text("登录")',
            '[role="button"]:has-text("登录")',
            'input[type="submit"]',
        ),
    )
    if submit is None:
        raise RuntimeError("Could not find a visible login submit control")
    submit.click()


def is_login_page(page: Page) -> bool:
    """Return whether the browser is still on the 58 passport login surface."""
    return "passport.58.com/login" in page.url


def detect_verification(page: Page) -> str | None:
    """Detect interactive verification text without attempting to bypass it."""
    body_text = page.locator("body").inner_text(timeout=5_000)
    return next((marker for marker in VERIFICATION_MARKERS if marker in body_text), None)


def save_auth_state(
    context: BrowserContext,
    state_file: Path,
    cookie_file: Path,
) -> int:
    """Save browser state and 58-scoped cookies with owner-only permissions."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    cookie_file.parent.mkdir(parents=True, exist_ok=True)

    context.storage_state(path=str(state_file))
    os.chmod(state_file, stat.S_IRUSR | stat.S_IWUSR)

    scoped_cookies = [
        cookie
        for cookie in context.cookies()
        if cookie.get("domain", "").lstrip(".").endswith("58.com")
    ]
    cookie_file.write_text(
        json.dumps(scoped_cookies, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(cookie_file, stat.S_IRUSR | stat.S_IWUSR)
    return len(scoped_cookies)


def request_api(
    context: BrowserContext,
    api_url: str,
    api_output: Path | None,
) -> None:
    """Call a 58 API with the authenticated browser context's cookie jar."""
    response = context.request.get(api_url, timeout=30_000)
    if not response.ok:
        raise RuntimeError(f"API request failed with HTTP {response.status}")

    content_type = response.headers.get("content-type", "")
    if "json" in content_type.lower():
        output = json.dumps(response.json(), ensure_ascii=False, indent=2)
    else:
        output = response.text()

    if api_output is None:
        print(output)
        return

    api_output.parent.mkdir(parents=True, exist_ok=True)
    api_output.write_text(output, encoding="utf-8")
    print(f"API response saved to {api_output}")


def run(playwright: Playwright, args: argparse.Namespace) -> None:
    """Launch Chromium, perform login, persist state, and optionally call an API."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    username = os.environ.get("VIP58_USERNAME", "").strip()
    password = os.environ.get("VIP58_PASSWORD", "")
    if not username or not password:
        raise RuntimeError(
            "Set VIP58_USERNAME and VIP58_PASSWORD in the process environment"
        )

    validate_api_url(args.api_url)

    browser = playwright.chromium.launch(headless=not args.headed)
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(args.timeout_ms)

    try:
        page.goto(args.login_url, wait_until="domcontentloaded")
        if is_login_page(page):
            fill_login_form(page, username, password)
            try:
                page.wait_for_url(
                    re.compile(r"^https://(?!passport\.58\.com/login)"),
                    timeout=args.timeout_ms,
                    wait_until="domcontentloaded",
                )
            except PlaywrightTimeoutError as error:
                marker = detect_verification(page)
                if marker:
                    raise RuntimeError(
                        f"Interactive verification required ({marker}); "
                        "run once with --headed or use a manually created auth state."
                    ) from error
                raise RuntimeError(
                    "Login did not complete. Check credentials or run once with --headed."
                ) from error

        cookie_count = save_auth_state(
            context,
            args.state_file,
            args.cookie_file,
        )
        if cookie_count == 0:
            raise RuntimeError("Login produced no cookies scoped to 58.com")

        print(f"Saved browser state to {args.state_file}")
        print(f"Saved {cookie_count} scoped cookies to {args.cookie_file}")

        if args.api_url:
            request_api(context, args.api_url, args.api_output)
    finally:
        context.close()
        browser.close()


def main() -> int:
    """Run the command and return a process exit code."""
    try:
        args = parse_args()
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "Playwright is not installed. Run: pip install playwright"
            ) from error

        with sync_playwright() as playwright:
            run(playwright, args)
        return 0
    except (RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
