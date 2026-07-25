"""9Router registration in the signup browser session — no second Camoufox launch needed.

The user is already logged in after signup, so we just navigate to the device
verification URL, click Continue → Allow, then POST poll to 9Router.
"""

from __future__ import annotations

import asyncio

from .utils.logger import log, log_err, log_ok


async def register_9router_in_session(
    page,
    email: str,
    password: str,
    nr_session,
    nr_base: str,
) -> bool:
    import requests as _req

    if not nr_session or not nr_base:
        log("   ⚠️ 9Router: no session/base, skipping")
        return False

    # 1. Trigger device code via 9Router API
    try:
        r = _req.get(
            f"{nr_base}/api/oauth/grok-cli/device-code",
            cookies=nr_session.cookies,
            timeout=30,
        )
        r.raise_for_status()
        dev = r.json()
        device_code = dev.get("device_code")
        code_verifier = dev.get("codeVerifier")
        verification_url = dev.get("verification_uri_complete")
        if not verification_url or not device_code:
            log_err("   ❌ 9Router: no verification_url in device-code response")
            return False
        log_ok(f"   ✅ 9Router device code: {dev.get('user_code', '?')}")
    except Exception as e:
        log_err(f"   ❌ 9Router device-code trigger failed: {e}")
        return False

    # 2. Navigate browser to verification URL (user already logged in)
    try:
        # Navigate to accounts.x.ai first to "warm up" domain session
        # (after signup, user lands on grok.com — cookies may be stale for accounts.x.ai)
        await page.goto("https://accounts.x.ai/", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)
        log(f"   → warmed up session on: {page.url}")

        # ponytail: re-navigate if redirect to sign-in (race condition)
        for nav_attempt in range(4):
            await page.goto(verification_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            log(f"   → landed on: {page.url}")
            if "/sign-in" not in page.url:
                break
            log(f"   → redirect to sign-in, retry nav ({nav_attempt+1}/4)")

        # 3. Click Continue (pre-filled user_code)
        try:
            continue_btn = page.locator('button:has-text("Continue")')
            await continue_btn.first.wait_for(state="visible", timeout=10000)
            await continue_btn.first.click(timeout=10000)
            log("   → clicked Continue")
            await asyncio.sleep(4)
        except Exception:
            log("   → no Continue button (may already be authorized)")

        # 4. Click Allow — loop with re-navigation on redirect
        for allow_attempt in range(6):
            try:
                allow_btn = page.locator('button:has-text("Allow")')
                await allow_btn.first.wait_for(state="visible", timeout=8000)
                await allow_btn.first.click(timeout=10000)
                log_ok("   ✅ clicked Allow — device authorized")
                await asyncio.sleep(3)
                break
            except Exception:
                cur = page.url
                if "/sign-in" in cur or "/login" in cur:
                    log(f"   → redirect to login, re-navigating ({allow_attempt+1}/6)")
                    await page.goto(verification_url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(4)
                else:
                    log(f"   → Allow button not found on {cur}, retrying...")
                    # Try clicking Continue in case we're on the user_code page
                    try:
                        cont = page.locator('button:has-text("Continue")')
                        await cont.first.wait_for(state="visible", timeout=5000)
                        await cont.first.click(timeout=10000)
                        await asyncio.sleep(4)
                    except Exception:
                        await asyncio.sleep(2)

    except Exception as e:
        log_err(f"   ⚠️ 9Router browser step failed: {e}")
        return False

    # 5. POST poll to 9Router — exchange device_code for token
    try:
        for attempt in range(6):
            r = _req.post(
                f"{nr_base}/api/oauth/grok-cli/poll",
                json={"deviceCode": device_code, "codeVerifier": code_verifier},
                cookies=nr_session.cookies,
                timeout=30,
            )
            data = r.json()
            if data.get("success"):
                log_ok(f"   ✅ 9Router stored token (poll attempt {attempt+1})")
                return True
            if data.get("error") == "authorization_pending":
                log(f"   → still pending (attempt {attempt+1}/6)")
                await asyncio.sleep(5)
                continue
            log_err(f"   ❌ 9Router poll failed: {data.get('error', 'unknown')}")
            return False
        log_err("   ❌ 9Router poll timeout after 30s")
        return False
    except Exception as e:
        log_err(f"   ❌ 9Router poll failed: {e}")
        return False
