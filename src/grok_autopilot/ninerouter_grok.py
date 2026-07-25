"""
Grok Autopilot — 9Router Grok-CLI OAuth Device Flow
====================================================
Automates the OAuth device authorization for grok-cli provider in 9Router.

Flow:
  1. GET /api/oauth/grok-cli/device-code on 9Router → device_code + verification_uri_complete
  2. Browser: open verification_uri_complete → sign out → login with account creds → Allow
  3. 9Router polls xAI device endpoint → receives tokens → stores new connection

Usage:
    python -m grok_autopilot.ninerouter_grok --accounts accounts/accounts.json --all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import requests

from .browser.camoufox import launch_browser
from .utils.logger import log, log_err, log_ok

# Default 9Router config — override via env vars, NEVER hardcode credentials
NINEROUTER_DEFAULT_HOST = os.environ.get("NINEROUTER_HOST", "http://localhost:20128")
NINEROUTER_DEFAULT_PASSWORD = os.environ.get("NINEROUTER_PASSWORD", "")


def ninerouter_login(
    base: str = NINEROUTER_DEFAULT_HOST,
    password: str = NINEROUTER_DEFAULT_PASSWORD,
) -> dict:
    """Login to 9Router, return cookie jar."""
    s = requests.Session()
    r = s.post(
        f"{base}/api/auth/login",
        json={"password": password},
        timeout=15,
    )
    r.raise_for_status()
    if not r.json().get("success"):
        raise RuntimeError(f"9Router login failed: {r.text}")
    log_ok(f"   ✅ 9Router login OK")
    return {"session": s, "base": base}


def trigger_device_code(nr: dict) -> dict:
    """Trigger OAuth device-code flow for grok-cli. Returns device_code response."""
    s = nr["session"]
    r = s.get(f"{nr['base']}/api/oauth/grok-cli/device-code", timeout=30)
    r.raise_for_status()
    d = r.json()
    log_ok(f"   ✅ Device code triggered: {d.get('user_code')}")
    log(f"   → verification URL: {d.get('verification_uri_complete')}")
    return d


def poll_ninerouter(nr: dict, device_code: str, code_verifier: str, timeout: int = 60) -> bool:
    """POST /api/oauth/grok-cli/poll to exchange device code for tokens.

    9Router grok-cli does NOT auto-poll — client must trigger this after
    the user clicks Allow in the browser. Returns True if token stored.
    """
    s = nr["session"]
    deadline = time.time() + timeout
    interval = 5
    while time.time() < deadline:
        try:
            r = s.post(
                f"{nr['base']}/api/oauth/grok-cli/poll",
                json={"deviceCode": device_code, "codeVerifier": code_verifier},
                timeout=15,
            )
            d = r.json()
        except Exception as e:
            log_err(f"   poll request failed: {e}")
            time.sleep(interval)
            continue

        if d.get("success"):
            log_ok(f"   ✅ 9Router stored token")
            return True
        err = d.get("error", "")
        if err == "authorization_pending":
            time.sleep(interval)
            continue
        if err in ("expired_token", "access_denied", "slow_down"):
            log_err(f"   poll error: {err} — {d.get('errorDescription','')}")
            return False
        # Unknown error — retry
        log(f"   poll: {d.get('error','?')} — retrying…")
        time.sleep(interval)

    log_err(f"   poll timeout ({timeout}s)")
    return False


async def authorize_device(
    verification_url: str,
    email: str,
    password: str,
    headless: bool = False,
    timeout: int = 120,
    mail_provider: str = "cloudflare",
    mailbox_secret: str | None = None,
    worker_url: str | None = None,
    signup_session: dict | None = None,
) -> bool:
    """Open verification URL in browser, login with account, authorize.

    Strategy:
      1. Open device verification URL.
      2. If signup_session cookies provided, inject them first (reuse signup session).
      3. If not logged in, attempt login with email + password.
      4. Click Allow on the authorize screen.

    Returns True if "Allow" was clicked successfully.
    """
    from .infra.temp_mail import create_temp_mail
    import os
    import re as _re
    import time as _time

    # Set up mail provider for OTP polling (login OTP)
    if mailbox_secret:
        os.environ["CF_MAILBOX_SECRET"] = mailbox_secret
    mail = create_temp_mail(provider=mail_provider)
    if hasattr(mail, "_last_address"):
        mail._last_address = email

    log(f"   🦊 Launching browser for OAuth authorization…")
    async with launch_browser(headless=headless) as browser:
        # Reuse signup session if provided (skip login entirely)
        if signup_session and signup_session.get("cookies"):
            try:
                # Camoufox: browser may have default context or need new one
                ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                await ctx.add_cookies(signup_session["cookies"])
                log_ok(f"   ✅ Injected {len(signup_session['cookies'])} signup cookies")
            except Exception as e:
                log(f"   ⚠️ cookie injection failed: {e}")

        page = await (browser.contexts[0] if browser.contexts else browser).new_page()

        async def block(route, req):
            if any(s in req.url.lower() for s in ("onetrust", "cookielaw")):
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block)
        await page.goto(verification_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(4)
        log(f"   → landed on {page.url}")

        # Step 1: Continue (pre-filled user_code page)
        continue_btn = page.locator('button:has-text("Continue")')
        try:
            await continue_btn.first.wait_for(state="visible", timeout=10000)
            await continue_btn.first.click(timeout=10000)
            log("   → clicked Continue")
            await asyncio.sleep(4)
        except Exception:
            log("   → no Continue button")

        # NOTE: Do NOT early-return on "Allow" button — signup session is NOT
        # OAuth-authorized. We must always go through explicit login flow
        # (email + password + login OTP) for 9Router to receive the callback.

        # Step 2: Sign out if already logged in as another account
        try:
            signout = page.locator('button:has-text("Sign out"), a:has-text("Sign out")')
            if await signout.first.is_visible(timeout=3000):
                await signout.first.click(timeout=5000)
                log("   → signed out previous account")
                await asyncio.sleep(3)
        except Exception:
            pass

        # Step 3: Login flow — only if we have a real password
        has_real_pwd = password and not password.startswith("(")
        if has_real_pwd:
            try:
                # If still on device page with Continue, click it first
                try:
                    cont = page.locator('button:has-text("Continue")')
                    if await cont.first.is_visible(timeout=2000):
                        await cont.first.click(timeout=5000)
                        await asyncio.sleep(3)
                except Exception:
                    pass

                email_login_btn = page.locator(
                    'button:has-text("Login with email"), button:has-text("Log in with email"), '
                    'button:has-text("Sign in with email"), a:has-text("Login with email")'
                )
                try:
                    await email_login_btn.first.wait_for(state="visible", timeout=5000)
                    await email_login_btn.first.click(timeout=5000)
                    log("   → clicked 'Login with email'")
                    await asyncio.sleep(3)
                except Exception:
                    pass

                # xAI often shows email+password on same screen
                email_input = page.locator('input[type="email"], input[name="email"]')
                await email_input.first.wait_for(state="visible", timeout=10000)
                await email_input.first.fill("")
                await email_input.first.fill(email)
                log(f"   → filled email {email}")
                await asyncio.sleep(0.4)

                pw_input = page.locator('input[type="password"]')
                try:
                    await pw_input.first.wait_for(state="visible", timeout=8000)
                except Exception:
                    # multi-step: continue after email only
                    btn = page.locator(
                        'button:has-text("Continue"), button:has-text("Next"), button[type="submit"]'
                    )
                    await btn.first.click(timeout=5000)
                    await asyncio.sleep(3)
                    await pw_input.first.wait_for(state="visible", timeout=15000)

                await pw_input.first.fill("")
                await pw_input.first.fill(password)
                await asyncio.sleep(0.4)
                # Prefer exact Login button (form has Login + Go back)
                clicked = False
                for sel in (
                    'button:has-text("Login")',
                    'button:has-text("Log in")',
                    'button:has-text("Sign in")',
                    'button[type="submit"]',
                ):
                    try:
                        b = page.locator(sel).first
                        if await b.count() and await b.is_visible():
                            await b.click(timeout=5000)
                            clicked = True
                            log(f"   → clicked login via {sel}")
                            break
                    except Exception:
                        continue
                if not clicked:
                    await page.keyboard.press("Enter")
                    log("   → pressed Enter on login form")
                await asyncio.sleep(6)
                log(f"   → post-login url: {page.url}")
            except Exception as e:
                log(f"   → login form issue: {e}")

            # Step 3.5: Login OTP (if Grok requires)
            try:
                otp_input = page.locator('input[name="code"]')
                await otp_input.first.wait_for(state="visible", timeout=20000)
                log("   → login OTP screen — polling email…")
                otp = await _poll_login_otp(mail, email, timeout=90)
                clean_otp = _re.sub(r"[^a-zA-Z0-9]", "", otp).upper()[:6]
                log_ok(f"   ✅ login OTP: {otp} → {clean_otp}")
                await otp_input.first.click()
                await page.keyboard.type(clean_otp, delay=80)
                await asyncio.sleep(1)
                try:
                    btn = page.locator(
                        'button:has-text("Confirm"), button:has-text("Confirm email"), button[type="submit"]'
                    )
                    await btn.first.click(timeout=5000)
                except Exception:
                    pass
                log("   → confirmed login OTP")
                await asyncio.sleep(5)
            except Exception:
                log("   → no login OTP screen")
        else:
            log("   ⚠️ no real password — relying on injected session only")

        # Step 4: Authorize Grok Build — click "Allow"
        # After login, Grok may redirect back to device verification page with
        # "Continue" button. Click it again, then wait for Allow.
        for attempt in range(3):
            try:
                allow_btn = page.locator('button:has-text("Allow")')
                if await allow_btn.first.is_visible(timeout=5000):
                    await allow_btn.first.click(timeout=10000)
                    log_ok("   ✅ clicked Allow — device authorized")
                    await asyncio.sleep(3)
                    return True
            except Exception:
                pass
            # Maybe back on device verification page — click Continue
            try:
                continue_btn2 = page.locator('button:has-text("Continue")')
                if await continue_btn2.first.is_visible(timeout=3000):
                    await continue_btn2.first.click(timeout=5000)
                    log(f"   → clicked Continue (attempt {attempt+1}, post-login)")
                    await asyncio.sleep(5)
                    continue
            except Exception:
                pass
            await asyncio.sleep(3)

        log_err("   ❌ Allow button not found after retries")
        try:
            state = await page.evaluate(
                '() => JSON.stringify({url: location.href, h1: document.querySelector("h1")?.textContent, body: document.body.innerText.substring(0,500)})'
            )
            log_err(f"   state: {state}")
        except Exception:
            pass
        return False


async def _poll_login_otp(mail, email: str, timeout: int = 90) -> str:
    """Poll for a fresh login OTP (skip old emails)."""
    import re as _re
    import time as _time

    seen_ids: set[str] = set()
    try:
        existing = mail.inbox(email)
        for m in existing:
            seen_ids.add(m.get("id", ""))
    except Exception:
        pass

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        try:
            msgs = mail.inbox(email)
        except Exception:
            await asyncio.sleep(5)
            continue
        new_msgs = [m for m in msgs if m.get("id") not in seen_ids]
        for m in new_msgs:
            subj = m.get("subject", "")
            if not _re.search(r"grok|x\.ai|xai|verif|code|spacex|confirm|login", subj, _re.I):
                continue
            try:
                full = mail.message(m["id"], address=email) if hasattr(mail, "_last_address") else mail.message(m["id"])
            except Exception:
                continue
            combined = subj + " " + (full.get("text", "") if full else "") + " " + (full.get("html", "") if full else "")
            m_subj = _re.search(r"code[:\s]+([A-Z0-9-]{4,12})", subj, _re.I)
            if m_subj:
                return m_subj.group(1)
            m_body = _re.search(r"\b([A-Z0-9]{2,4}-[A-Z0-9]{2,4})\b", combined)
            if m_body:
                return m_body.group(1)
            m_num = _re.search(r"\b(\d{6})\b", combined)
            if m_num and m_num.group(1) != "333333":
                return m_num.group(1)
        await asyncio.sleep(4)
    raise TimeoutError(f"No login OTP within {timeout}s")


async def register_account_to_ninerouter(
    account: dict,
    nr: dict | None = None,
    headless: bool = False,
    mailbox_secret: str | None = None,
    worker_url: str | None = None,
) -> bool:
    """Register one account to 9Router via grok-cli device flow."""
    email = account.get("email", "")
    password = account.get("password", "")
    signup_session = account.get("session_cookies") or account.get("signup_session")
    if not email:
        log_err(f"   ❌ account missing email")
        return False

    log("─" * 60)
    log(f"🔗 Registering {email} to 9Router grok-cli")

    if nr is None:
        nr = ninerouter_login()

    dc = trigger_device_code(nr)
    verification_url = dc["verification_uri_complete"]

    ok = await authorize_device(
        verification_url,
        email,
        password,
        headless=headless,
        mailbox_secret=mailbox_secret,
        worker_url=worker_url,
        signup_session=signup_session,
    )
    if not ok:
        log_err(f"   ❌ Authorization failed for {email}")
        return False

    # 9Router grok-cli does NOT auto-poll — client must POST /poll to exchange
    # the device code for tokens after the user clicks Allow in browser.
    log("   ⏳ polling 9Router to exchange device code for tokens…")
    stored = poll_ninerouter(
        nr,
        device_code=dc["device_code"],
        code_verifier=dc["codeVerifier"],
        timeout=60,
    )
    if not stored:
        log_err(f"   ❌ 9Router did not store token for {email}")
        return False

    log_ok(f"   ✅ Verified: {email} registered as grok-cli in 9Router")
    return True


async def run(accounts_file: str, headless: bool = False, only: list[int] | None = None, mailbox_secret: str | None = None, worker_url: str | None = None):
    """Register all accounts from accounts.json to 9Router."""
    f = Path(accounts_file)
    if not f.exists():
        log_err(f"accounts file not found: {f}")
        return 1
    accounts = json.loads(f.read_text())
    if only:
        accounts = [accounts[i] for i in only if i < len(accounts)]

    nr = ninerouter_login()
    ok = 0
    for i, acct in enumerate(accounts, 1):
        log(f"\n[{i}/{len(accounts)}]")
        try:
            if await register_account_to_ninerouter(acct, nr=nr, headless=headless, mailbox_secret=mailbox_secret, worker_url=worker_url):
                ok += 1
        except Exception as e:
            log_err(f"   ❌ failed: {e}")
        if i < len(accounts):
            log("   ⏸️  15s cooldown…")
            await asyncio.sleep(15)

    log(f"\n{'='*60}")
    log(f"DONE: {ok}/{len(accounts)} accounts registered to 9Router")
    return 0 if ok == len(accounts) else 1


def main() -> int:
    import os
    p = argparse.ArgumentParser(prog="grok-autopilot-ninerouter")
    p.add_argument("--accounts", default="accounts/accounts.json")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--only", type=int, nargs="*", help="Indices (0-based) to register only")
    p.add_argument("--mailbox-secret", default=os.environ.get("CF_MAILBOX_SECRET", ""))
    p.add_argument("--worker-url", default=os.environ.get("WORKER_URL", ""))
    args = p.parse_args()
    return asyncio.run(run(args.accounts, headless=args.headless, only=args.only, mailbox_secret=args.mailbox_secret, worker_url=args.worker_url))


if __name__ == "__main__":
    sys.exit(main())
