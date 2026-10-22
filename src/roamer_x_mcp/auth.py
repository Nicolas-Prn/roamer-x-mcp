"""Obtain the user's IBSS bearer token by driving the real IBSS Hub in a local browser.

The `uob` tenant signs users in with Microsoft Entra ID through the Hub (an MSAL.js single-page app), which
keeps a ~65 min token in `localStorage["token"]` and renews it silently while the Microsoft session lives.
We never handle the user's password: `login` opens a visible browser where the user signs in themselves,
in a dedicated persistent profile (`~/.config/roamer-x-mcp/browser`). Later refreshes reopen that profile
headless, let the Hub renew its token, and read it back.

When the token has expired, the Hub logs out and wipes its storage (so its silent MSAL renewal is gone).
The refresh then signs in again the way the user did: it re-accepts the IBSS consent dialog the user already
accepted, enters the user's email, and lets Microsoft sign in silently with the profile's persistent
"stay signed in" cookie. If Microsoft asks for a password or MFA, it gives up and asks for `login`.

Playwright's sync API cannot run inside the MCP server's asyncio loop, so the server refreshes through a
subprocess: `python -m roamer_x_mcp.auth refresh`.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import time

from . import config

PROFILE_DIR = config.HOME / "browser"
# Renew a bit before expiry so a token never dies in the middle of a tool call.
MIN_VALIDITY_S = 5 * 60


class LoginRequiredError(config.NotConfiguredError):
    pass


def token_expiry(token: str) -> float:
    """`exp` claim of a JWT (seconds since epoch), 0 if it cannot be read."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError):
        return 0.0


def is_fresh(token: str | None) -> bool:
    return bool(token) and token_expiry(token) - time.time() > MIN_VALIDITY_S


def _launch(p, headless: bool):
    kwargs = dict(user_data_dir=str(PROFILE_DIR), headless=headless, viewport={"width": 1280, "height": 860})
    PROFILE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except Exception:
        # No Google Chrome installed: fall back to Playwright's Chromium (`playwright install chromium`).
        return p.chromium.launch_persistent_context(**kwargs)


def _read_token(page) -> str | None:
    try:
        raw = page.evaluate("() => localStorage.getItem('token')")
    except Exception:
        return None  # page is navigating (e.g. Microsoft redirect)
    return raw.strip('"') if raw else None


def email_of(token: str | None) -> str:
    try:
        payload = (token or "").split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return ""
    return claims.get("email") or claims.get("preferred_username") or ""


def _sign_in_again(page, email: str) -> None:
    """Drive the Hub login page: consent dialog (enabled once its text is scrolled to the end), email, Next."""
    accept = page.get_by_role("button", name="Accept")
    try:
        accept.wait_for(state="visible", timeout=5000)
        page.locator("[role=dialog] .text-justify").evaluate("e => e.scrollTop = e.scrollHeight")
        page.wait_for_function(
            "() => [...document.querySelectorAll('button')].some(b => b.innerText.trim() === 'Accept' && !b.disabled)",
            timeout=5000,
        )
        accept.click()
    except Exception:
        pass  # no consent dialog this time
    page.fill("#email", email)
    page.click("#login")


def _microsoft_wants_user(page) -> bool:
    # Password, MFA or account picker pages: silent SSO did not work, a human is needed.
    return any(page.locator(sel).count() for sel in (
        "input[type=password]", "#idDiv_SAOTCAS_Title", "#idDiv_SAOTCC_Title", "#tilesHolder",
    ))


def _wait_for_fresh_token(page, timeout_s: float, interactive: bool, email: str = "") -> str | None:
    deadline = time.time() + timeout_s
    signed_in_again = False
    while time.time() < deadline:
        url = page.url
        if "iconics.cloud" in url:
            token = _read_token(page)
            if is_fresh(token):
                return token
            if not interactive and "/login" in url and email and not signed_in_again:
                try:
                    _sign_in_again(page, email)
                except Exception:
                    return None
                signed_in_again = True
        elif not interactive and "login.microsoftonline.com" in url and _microsoft_wants_user(page):
            return None
        page.wait_for_timeout(1000)
    return None


def browser_token(tenant: str, interactive: bool, email: str = "") -> str:
    """Open the Hub and return a fresh token.

    interactive: visible window, wait for the user to sign in. Otherwise headless, signing in again with
    `email` if the Hub session has expired.
    """
    from playwright.sync_api import sync_playwright

    hub = config.Settings(tenant, "").hub_url
    with sync_playwright() as p:
        ctx = _launch(p, headless=not interactive)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(hub + "/", wait_until="domcontentloaded")
            # Opening "my bookings" makes the Hub call the API, which triggers its silent token renewal.
            page.wait_for_timeout(3000)
            if "iconics.cloud" in page.url and "/login" not in page.url:
                page.goto(hub + "/flex/bookings/mine", wait_until="domcontentloaded")
            token = _wait_for_fresh_token(page, 600 if interactive else 60, interactive, email)
        finally:
            ctx.close()
    if not token:
        raise LoginRequiredError(
            "Roamer X session expired: run `roamer-x-mcp login` in a terminal and sign in again."
        )
    return token


def refresh_in_subprocess() -> None:
    """Renew the stored token headlessly (used by the MCP server)."""
    res = subprocess.run(
        [sys.executable, "-m", "roamer_x_mcp.auth", "refresh"], capture_output=True, text=True, timeout=120
    )
    if res.returncode != 0:
        message = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "token refresh failed"
        raise LoginRequiredError(message)


def _refresh_main() -> int:
    stored = config.load(require_token=False)
    try:
        token = browser_token(stored.tenant, interactive=False, email=email_of(stored.token))
    except LoginRequiredError as e:
        print(e, file=sys.stderr)
        return 2
    config.save(config.Settings(stored.tenant, token))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["refresh"]:
        sys.exit(_refresh_main())
    print("usage: python -m roamer_x_mcp.auth refresh", file=sys.stderr)
    sys.exit(64)
