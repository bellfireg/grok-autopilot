"""
Grok Autopilot — Registration Pipeline
========================================
End-to-end Grok (accounts.x.ai) account creation:

    1. Generate temp email (mail.tm default)
    2. Navigate accounts.x.ai/sign-up → click "Sign up with email"
    3. Fill email → submit → "Verify your email" OTP screen appears
    4. Poll temp inbox → extract 6-digit code from Grok email
    5. Enter OTP → submit
    6. If password screen appears → set random strong password
    7. Capture session cookies + any API token surfaced post-login
    8. Persist account → emit JSON for 9router registration (optional)

Zero captcha. No Turnstile in email signup path (verified 2026-07-20).
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .browser.camoufox import launch_browser
from .infra.temp_mail import TempMailProvider, create_temp_mail
from .nr_in_session import register_9router_in_session
from .utils.logger import log, log_err, log_ok

SIGNUP_URL = "https://accounts.x.ai/sign-up"
# After successful email verification, Grok redirects to grok.com dashboard.
DASHBOARD_URL_HINT = "grok.com"

OTP_RE = re.compile(r"\b(\d{6})\b")  # 6-digit OTP (numeric)
# Grok/xAI OTP is alphanumeric in subject: "SpaceXAI confirmation code: S23-XSW"
OTP_ALNUM_RE = re.compile(r"\b([A-Z0-9]{2,4}-[A-Z0-9]{2,4})\b")  # XXX-XXX format
OTP_SUBJECT_RE = re.compile(r"code[:\s]+([A-Z0-9-]{4,12})", re.I)
PASSWORD_RE = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z\d!@#$%^&*]{12,}$"
)


@dataclass
class GrokAccount:
    email: str
    password: str
    mail_jwt: str  # mail.tm jwt for inbox access
    session_cookies: dict
    created_at: float
    status: str  # "verified", "password_set", "partial"
    notes: str = ""


async def _block_oneTrust(page) -> None:
    """Block OneTrust cookie consent at network level — never loads.

    Also inject Turnstile interceptor script via add_init_script.
    Also log signup API responses for debugging silent rejections.
    """
    from .captcha.turnstile import INTERCEPT_SCRIPT

    async def route_handler(route, request):
        url = request.url.lower()
        if any(s in url for s in ("onetrust", "cookielaw")):
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", route_handler)
    # Log API responses for debugging silent rejections
    async def response_handler(response):
        try:
            u = response.url.lower()
            if any(k in u for k in ("sign-up", "signup", "verify", "code", "otp", "account", "auth", "x.ai")):
                status = response.status
                body = ""
                try:
                    body = await response.text()
                except Exception:
                    pass
                if body and len(body) > 300:
                    body = body[:300] + "..."
                log(f"   📡 {response.url[:80]} → {status} {body[:200]}")
        except Exception:
            pass

    page.on("response", lambda r: asyncio.ensure_future(response_handler(r)))
    # Log ALL POST requests — find what submit actually sends
    def request_handler(request):
        if request.method == "POST":
            log(f"   📤 POST {request.url[:100]}")
    page.on("request", request_handler)
    # Capture console messages — JS errors that block submit
    def console_handler(msg):
        if msg.type in ("error", "warning"):
            log(f"   💬 console.{msg.type}: {msg.text[:200]}")
    page.on("console", console_handler)
    # Inject Turnstile interceptor — runs before any page script
    try:
        await page.add_init_script(INTERCEPT_SCRIPT)
    except Exception as e:
        log(f"   ⚠️ init script inject failed: {e}")


async def _click_signup_with_email(page) -> None:
    """Click the 'Sign up with email' button on the multi-option page."""
    btn = await page.query_selector('button:has-text("Sign up with email")')
    if not btn:
        # Fallback: scan all buttons for "email" text
        btns = await page.query_selector_all("button")
        for b in btns:
            t = ((await b.text_content()) or "").strip()
            if "email" in t.lower():
                btn = b
                break
    if not btn:
        raise RuntimeError("Sign up with email button not found")
    await btn.click()
    log("   → clicked 'Sign up with email'")


async def _fill_email_and_submit(page, email: str) -> None:
    """Fill email, solve Turnstile if present, then submit.

    xAI added Turnstile to the email step (2026-07-24). Without a token,
    the React onSubmit silently aborts — no POST, no error, no transition.
    """
    import os as _os
    import json as _json
    from .captcha import solve_turnstile, solve_turnstile_local, extract_turnstile_sitekey, inject_turnstile_token

    email_input = page.locator('input[type="email"]')
    await email_input.wait_for(state="visible", timeout=15000)
    await email_input.click()
    await asyncio.sleep(0.3)
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Delete")
    await asyncio.sleep(0.2)
    await page.keyboard.type(email, delay=50)
    await asyncio.sleep(2)

    # Check for Turnstile on email step
    # xAI loads turnstile/v0/api.js but may use implicit/invisible mode.
    # Extract sitekey from page source, solve via 2captcha, inject token.
    ts_info = await page.evaluate("""() => {
        const info = {
            captcha_data: window.__captcha_data ? JSON.stringify(window.__captcha_data) : null,
            has_turnstile: typeof window.turnstile !== 'undefined',
            turnstile_keys: typeof window.turnstile !== 'undefined' ? Object.keys(window.turnstile || {}) : [],
            hidden_inputs: [...document.querySelectorAll('input[name="cf-turnstile-response"], input[name="cf_challenge_response"]')].map(i => ({name: i.name, value: i.value?.substring(0,30)})),
            ts_divs: [...document.querySelectorAll('.cf-turnstile,[data-sitekey]')].length,
            sitekey_attr: document.querySelector('[data-sitekey]')?.getAttribute('data-sitekey'),
            ts_containers: [...document.querySelectorAll('[data-sitekey], .cf-turnstile')].map(e => ({tag: e.tagName, sitekey: e.getAttribute('data-sitekey'), cls: e.className})),
            scripts: [...document.querySelectorAll('script[src]')].map(s => s.src).filter(s => s.includes('turnstile') || s.includes('challenge')),
            // Check Next.js __NEXT_DATA__ for sitekey
            next_data_sitekey: (() => {
                const el = document.getElementById('__NEXT_DATA__');
                if (!el) return null;
                try {
                    const txt = el.textContent;
                    const m = txt.match(/0x[A-Za-z0-9_-]{10,}/);
                    return m ? m[0] : null;
                } catch(e) { return null; }
            })(),
            // Search entire HTML for sitekey pattern
            html_sitekey: (() => {
                const m = document.documentElement.innerHTML.match(/0x[A-Za-z0-9_-]{10,}/);
                return m ? m[0] : null;
            })()
        };
        return JSON.stringify(info, null, 2);
    }""")
    log(f"   🔍 Turnstile probe:\n{ts_info}")

    try:
        ts = _json.loads(ts_info)
    except Exception:
        ts = {}

    captcha_data = ts.get("captcha_data")
    # Detect sitekey from any source
    sitekey = None
    if captcha_data:
        try:
            cd = _json.loads(captcha_data)
            sitekey = cd.get("sitekey")
        except Exception:
            pass
    if not sitekey:
        sitekey = ts.get("sitekey_attr") or ts.get("next_data_sitekey") or ts.get("html_sitekey")

    has_turnstile = bool(sitekey) or bool(captcha_data) or ts.get("ts_divs", 0) > 0 or bool(ts.get("hidden_inputs"))

    # If no sitekey found immediately, wait and retry — Turnstile loads lazily
    if not sitekey:
        for _ in range(10):
            await asyncio.sleep(2)
            sitekey = await page.evaluate("""() => {
                // Check if turnstile widget appeared
                const el = document.querySelector('[data-sitekey]');
                if (el) return el.getAttribute('data-sitekey');
                // Check __NEXT_DATA__
                const nd = document.getElementById('__NEXT_DATA__');
                if (nd) {
                    const m = nd.textContent.match(/0x[A-Za-z0-9_-]{10,}/);
                    if (m) return m[0];
                }
                // Check all script tags
                for (const s of document.querySelectorAll('script')) {
                    const m = (s.textContent || '').match(/0x[A-Za-z0-9_-]{10,}/);
                    if (m) return m[0];
                }
                // Check iframes
                for (const f of document.querySelectorAll('iframe')) {
                    const m = (f.src || '').match(/0x[A-Za-z0-9_-]{10,}/);
                    if (m) return m[0];
                }
                return null;
            }""")
            if sitekey:
                has_turnstile = True
                break

    if sitekey:
        log(f"   → Turnstile sitekey found: {sitekey[:40]}")
        # Try self-hosted solver first (free), fall back to 2captcha
        token = solve_turnstile_local(page.url, sitekey, action=None, cdata=None)
        if not token:
            log("   → local solver failed, trying 2captcha fallback...")
            api_key = _os.environ.get("TWOCAPTCHA_KEY", "")
            if not api_key:
                try:
                    with open(_os.path.expanduser("~/.2captcha_key")) as f:
                        api_key = f.read().strip()
                except Exception:
                    pass
            if api_key:
                log(f"   → solving email-step Turnstile via 2captcha...")
                token = solve_turnstile(api_key, page.url, sitekey, action=None, cdata=None, timeout=180)
        if token:
            await inject_turnstile_token(page, token)
            await asyncio.sleep(3)
            log("   ✅ email-step Turnstile solved")
        else:
            log_err("   ⚠️ email-step Turnstile solve failed — submitting anyway")
    else:
        log("   → no Turnstile sitekey found anywhere — submitting without")

    # Submit: button click first (most reliable for React), then Enter, then form.requestSubmit
    for attempt in range(3):
        if attempt == 0:
            sub = await page.query_selector('button[type="submit"]:has-text("Sign up")')
            if not sub:
                sub = await page.query_selector('button[type="submit"]')
            if sub:
                await sub.click()
                log(f"   → clicked submit (attempt 1/3)")
            else:
                await page.keyboard.press("Enter")
                log(f"   → pressed Enter (no button found, attempt 1/3)")
        elif attempt == 1:
            await page.keyboard.press("Enter")
            log(f"   → pressed Enter (attempt 2/3)")
        else:
            await page.evaluate("""() => {
                const form = document.querySelector('form');
                if (form) form.requestSubmit();
            }""")
            log(f"   → form.requestSubmit() (attempt 3/3)")
        await asyncio.sleep(3)
        otp_input = await page.query_selector('input[name="code"]')
        if otp_input:
            log("   → OTP input detected")
            return
        try:
            current_h1 = (await page.locator("h1").first.text_content() or "").strip()
        except Exception:
            current_h1 = ""
        if "verify" in current_h1.lower():
            log("   → reached OTP screen")
            return
        if attempt < 2:
            log(f"   → still on email form, retrying...")
            await asyncio.sleep(1)
    log(f"   → submitted email {email} (best effort)")


async def _wait_for_otp_screen(page, timeout: int = 15) -> bool:
    """Wait for 'Verify your email' screen to appear."""
    try:
        await page.wait_for_selector('input[name="code"]', timeout=timeout * 1000)
        return True
    except Exception:
        # Capture current state for debugging
        try:
            import time as _t
            await page.screenshot(path=f"/tmp/grok_otp_fail_{int(_t.time())}.png")
            state = await page.evaluate(
                """() => JSON.stringify({
                    url: location.href,
                    h1: document.querySelector('h1')?.textContent,
                    h2: document.querySelector('h2')?.textContent,
                    inputs: [...document.querySelectorAll('input')].map(i => ({type:i.type,name:i.name,placeholder:i.placeholder,value:i.value?.substring(0,40)})),
                    errors: [...document.querySelectorAll('[class*="error" i],[role="alert"],[class*="invalid" i]')].map(e => (e.textContent||'').trim().substring(0,300)).filter(Boolean),
                    body: document.body.innerText.substring(0, 800)
                }, null, 2)"""
            )
            import sys
            print(f"[DEBUG] OTP-screen state:\n{state}", file=sys.stderr)
        except Exception:
            pass
        errs = await page.evaluate(
            '''() => [...document.querySelectorAll('[class*="error"],[role="alert"]')]
                        .map(e => e.textContent.trim()).filter(Boolean).join(" | ")'''
        )
        if errs:
            raise RuntimeError(f"Submit error: {errs}")
        raise RuntimeError("OTP input never appeared")


async def _extract_otp_from_email(text: str, subject: str = "") -> str | None:
    """Extract OTP from Grok verification email.

    xAI sends OTP in subject line as "SpaceXAI confirmation code: S23-XSW"
    (alphanumeric, hyphenated). Body may also contain 6-digit numeric code
    as fallback for older format.
    """
    # Priority 1: subject line — alphanumeric XXX-XXX format
    if subject:
        m = OTP_SUBJECT_RE.search(subject)
        if m:
            return m.group(1)
        m = OTP_ALNUM_RE.search(subject)
        if m:
            return m.group(1)
        # Plain 6-digit in subject
        m = OTP_RE.search(subject)
        if m:
            return m.group(1)

    # Priority 2: body — alphanumeric format first (real code)
    m = OTP_ALNUM_RE.search(text)
    if m:
        return m.group(1)

    # Priority 3: body — 6-digit numeric (filter out placeholder 333333
    # which appears in Grok's email template example text)
    for line in text.splitlines():
        if re.search(r"code|verify|otp", line, re.I):
            m = OTP_RE.search(line)
            if m and m.group(1) != "333333":  # skip template placeholder
                return m.group(1)
    # Fallback: first 6-digit, skip 333333
    for m in OTP_RE.finditer(text):
        if m.group(1) != "333333":
            return m.group(1)
    return None


async def _poll_for_otp(
    mail: TempMailProvider,
    email: str,
    jwt: str | None = None,  # ponytail: kept for signature compat; MailTm stores internally
    timeout: int = 120,
) -> str:
    """Poll temp inbox until Grok verification email arrives. Return OTP."""
    deadline = time.time() + timeout
    last_seen_count = 0
    while time.time() < deadline:
        try:
            msgs = mail.inbox(email)
        except Exception as e:
            log_err(f"   inbox poll err: {e}")
            await asyncio.sleep(5)
            continue

        if len(msgs) != last_seen_count:
            last_seen_count = len(msgs)
            log(f"   📨 inbox: {len(msgs)} message(s)")

        for m in msgs:
            subj = m.get("subject", "")
            if not re.search(r"grok|x\.ai|xai|verif|code|spacex|confirm", subj, re.I):
                continue
            try:
                # CloudflareProvider needs address for Bearer auth
                full = (
                    mail.message(m["id"], address=email)
                    if hasattr(mail, "_last_address")
                    else mail.message(m["id"])
                ) or {}
            except Exception as e:
                log_err(f"   message fetch err: {e}")
                continue
            otp = await _extract_otp_from_email(
                full.get("text", "") + " " + full.get("html", ""),
                subject=subj,
            )
            if otp:
                log_ok(f"   ✅ OTP extracted: {otp} (from subject='{subj[:50]}')")
                return otp

        await asyncio.sleep(4)

    raise TimeoutError(f"No Grok OTP email within {timeout}s")


async def _enter_otp_and_confirm(page, otp: str) -> None:
    """Fill OTP then leave the verify screen.

    xAI sometimes auto-submits at 6 chars, sometimes needs Confirm email.
    """
    clean_otp = re.sub(r"[^a-zA-Z0-9]", "", otp).upper()
    if len(clean_otp) > 6:
        clean_otp = clean_otp[:6]
    log(f"   → typing OTP '{otp}' → cleaned '{clean_otp}'")

    code_input = page.locator('input[name="code"]')
    await code_input.wait_for(state="visible", timeout=10000)
    await code_input.click()
    await asyncio.sleep(0.3)
    await page.keyboard.type(clean_otp, delay=100)

    # Wait briefly for auto-submit; if still on verify screen, click Confirm.
    for i in range(8):
        await asyncio.sleep(1)
        # password screen or complete-signup = done
        if await page.query_selector('input[type="password"], input[name="givenName"], input[name="password"]'):
            log("   → post-OTP advanced (password/name screen)")
            return
        h1 = (await page.locator("h1").first.text_content() or "").strip().lower()
        if "verify" not in h1 and "confirm" not in h1:
            log(f"   → left verify screen (h1={h1[:40]!r})")
            return

    # Still on verify — click Confirm email
    log("   → auto-submit missed; clicking Confirm email")
    for sel in (
        'button:has-text("Confirm email")',
        'button[type="submit"]',
        'text=Confirm email',
    ):
        try:
            btn = page.locator(sel).first
            if await btn.count() and await btn.is_visible():
                await btn.click()
                break
        except Exception:
            continue
    # wait for password screen
    for _ in range(15):
        await asyncio.sleep(1)
        if await page.query_selector('input[type="password"], input[name="givenName"], input[name="password"]'):
            log("   → password/name screen after Confirm")
            return
    log("   → post-confirm wait done; continuing with current screen")


async def _set_password_via_forgot_flow(page, email: str, mail, jwt: str) -> str:
    """Set a real password via the name+password+Turnstile screen after OTP.

    Per Bell 2026-07-20: after OTP confirm, Grok shows a screen with:
      - name input
      - password input (maybe 2: password + confirm)
      - Cloudflare Turnstile 2FA
    We solve Turnstile via 2captcha, fill name + password, submit.
    """
    import os as _os
    import secrets as _sec
    from .captcha import solve_turnstile, solve_turnstile_local, extract_turnstile_sitekey, inject_turnstile_token

    # Capture post-OTP screen DOM
    log("   🔍 Capturing post-OTP screen DOM…")
    state = await page.evaluate(
        """() => JSON.stringify({
            url: location.href,
            h1: document.querySelector("h1")?.textContent,
            inputs: [...document.querySelectorAll("input")].map(i => ({type:i.type,name:i.name,id:i.id,placeholder:i.placeholder,maxlength:i.maxLength,pattern:i.pattern,autocomplete:i.autocomplete})),
            buttons: [...document.querySelectorAll("button")].slice(0,10).map(b => ({text:(b.textContent||'').trim().substring(0,50), type:b.type, disabled:b.disabled})),
            ts_sitekey: document.querySelector('[data-sitekey]')?.getAttribute('data-sitekey'),
            ts_divs: [...document.querySelectorAll('.cf-turnstile,[data-sitekey]')].length,
            iframes: [...document.querySelectorAll('iframe')].map(f => (f.src||'').substring(0,150)),
            body: document.body.innerText.substring(0, 1000)
        }, null, 2)"""
    )
    log(f"   post-OTP state:\n{state}")

    # Check if we're actually on the name+password+Turnstile screen
    has_password_input = await page.query_selector('input[type="password"]')
    if not has_password_input:
        log_err("   ❌ no password input on post-OTP screen — flow may differ")
        raise RuntimeError("expected name+password screen, got something else")

    # Turnstile detection: window.__captcha_data (intercepted) OR DOM markers
    captcha_data = await page.evaluate(
        "() => window.__captcha_data ? JSON.stringify(window.__captcha_data) : null"
    )
    has_turnstile = bool(captcha_data) or bool(
        await page.query_selector(
            'iframe[src*="challenges.cloudflare.com"], input[name="cf-turnstile-response"], [data-sitekey], .cf-turnstile'
        )
    )
    if captcha_data:
        log_ok(f"   ✅ Turnstile intercepted: {captcha_data[:200]}")

    # Step 1: fill First name + Last name (xAI uses givenName/familyName)
    first_name = "Test" + _sec.token_hex(2)
    last_name = "User" + _sec.token_hex(2)
    for sel, val, label in [
        ('input[name="givenName"]', first_name, "First name"),
        ('input[name="familyName"]', last_name, "Last name"),
    ]:
        inp = page.locator(sel)
        try:
            await inp.first.wait_for(state="visible", timeout=5000)
            await inp.first.click()
            await asyncio.sleep(0.2)
            await page.keyboard.type(val, delay=50)
            log(f"   → filled {label}: {val}")
            await asyncio.sleep(0.3)
        except Exception as e:
            log_err(f"   ⚠️ {label} fill failed: {e}")

    # Step 2: fill password (single input, name="password")
    password = _sec.token_urlsafe(16) + "!1Aa"
    password = re.sub(r"[^A-Za-z0-9!]", "", password)[:24]
    if not (re.search(r"[a-z]", password) and re.search(r"[A-Z]", password) and re.search(r"\d", password)):
        password = _sec.token_urlsafe(16) + "!1Aa"
    pw_input = page.locator('input[name="password"]')
    try:
        await pw_input.first.wait_for(state="visible", timeout=5000)
        await pw_input.first.click()
        await asyncio.sleep(0.2)
        await page.keyboard.type(password, delay=30)
        log(f"   → filled password ({len(password)} chars)")
        await asyncio.sleep(1)
    except Exception as e:
        log_err(f"   ⚠️ password fill failed: {e}")
        raise

    # Step 3: solve Turnstile if present
    if has_turnstile:
        # Prefer intercepted data (has sitekey + callback)
        sitekey = None
        action = None
        cdata = None
        if captcha_data:
            import json as _json
            try:
                cd = _json.loads(captcha_data)
                sitekey = cd.get("sitekey")
                action = cd.get("action")
                cdata = cd.get("cData")
            except Exception:
                pass
        if not sitekey:
            sitekey = await extract_turnstile_sitekey(page)
        if sitekey:
            # Try self-hosted solver first (free), fall back to 2captcha
            log(f"   → solving Turnstile (sitekey={sitekey[:30]}, action={action}, cdata={cdata})")
            token = solve_turnstile_local(page.url, sitekey, action=action, cdata=cdata)
            if not token:
                log("   → local solver failed, trying 2captcha fallback...")
                api_key = _os.environ.get("TWOCAPTCHA_KEY", "")
                if not api_key:
                    try:
                        with open(_os.path.expanduser("~/.2captcha_key")) as f:
                            api_key = f.read().strip()
                    except Exception:
                        pass
                if not api_key or len(api_key) != 32:
                    log_err(f"   ❌ 2captcha key invalid (len={len(api_key)}) — set TWOCAPTCHA_KEY env or ~/.2captcha_key")
                    raise RuntimeError("2captcha key missing/invalid")
                token = solve_turnstile(api_key, page.url, sitekey, action=action, cdata=cdata, timeout=180)
            if not token:
                raise RuntimeError("Turnstile solve failed (both local and 2captcha)")
            await inject_turnstile_token(page, token)
            await asyncio.sleep(2)
        else:
            log_err("   ⚠️ Turnstile present but sitekey not extractable — proceeding without solve")

    # Step 4: verify no validation errors before submit
    await asyncio.sleep(1)
    errors = await page.evaluate(
        '''() => [...document.querySelectorAll('[class*="error" i],[role="alert"]')]
                    .map(e => e.textContent.trim())
                    .filter(Boolean).join(" | ")'''
    )
    if errors and "must provide" in errors.lower():
        log_err(f"   ⚠️ validation errors before submit: {errors}")
        # Don't abort — submit may still proceed if Turnstile + password valid

    # Step 5: submit — button text is "Complete sign up"
    submit_btn = page.locator('button:has-text("Complete sign up"), button[type="submit"]')
    try:
        await submit_btn.first.wait_for(state="visible", timeout=5000)
        # Wait for enabled
        for _ in range(10):
            disabled = await submit_btn.first.get_attribute("disabled")
            if not disabled:
                break
            await asyncio.sleep(0.5)
        await submit_btn.first.click(timeout=10000)
        log_ok(f"   ✅ submitted name+password+Turnstile")
    except Exception as e:
        log_err(f"   submit failed: {e}")
        raise

    await asyncio.sleep(8)

    # Check for post-submit errors (validation failed)
    post_errors = await page.evaluate(
        '''() => [...document.querySelectorAll('[class*="error" i],[role="alert"]')]
                    .map(e => e.textContent.trim())
                    .filter(Boolean).join(" | ")'''
    )
    if post_errors:
        log_err(f"   ⚠️ post-submit errors: {post_errors}")
        raise RuntimeError(f"signup submit had errors: {post_errors}")

    log_ok(f"   ✅ Password set: {password}")
    return password


async def _set_password_if_prompted(page, timeout: int = 15) -> str | None:
    """If password screen appears, set a random strong password. Return it."""
    try:
        await page.wait_for_selector(
            'input[type="password"]', timeout=timeout * 1000
        )
    except Exception:
        # Capture state for debugging — maybe name/birthday step instead
        try:
            state = await page.evaluate(
                """() => JSON.stringify({
                    url: location.href,
                    h1: document.querySelector('h1')?.textContent,
                    inputs: [...document.querySelectorAll('input')].map(i => ({type:i.type,name:i.name,placeholder:i.placeholder,id:i.id})),
                    buttons: [...document.querySelectorAll('button')].slice(0,5).map(b => (b.textContent||'').trim().substring(0,40)),
                    body: document.body.innerText.substring(0, 500)
                }, null, 2)"""
            )
            import sys
            print(f"[DEBUG] post-OTP state:\n{state}", file=sys.stderr)
        except Exception:
            pass
        return None  # No password step — Grok may auto-login or ask for more

    password = secrets.token_urlsafe(16) + "!1Aa"
    inputs = await page.query_selector_all('input[type="password"]')
    for inp in inputs:
        await inp.fill(password)
    await asyncio.sleep(1)
    # Re-query submit button fresh (React may re-render)
    try:
        btn = page.locator('button[type="submit"]')
        await btn.first.wait_for(state="attached", timeout=5000)
        await btn.first.click(timeout=10000)
    except Exception as e:
        log_err(f"   password submit click failed: {e}")
    log("   → password set")
    return password


async def _capture_session(page) -> dict:
    """Capture all cookies + localStorage after successful auth."""
    cookies = await page.context.cookies()
    storage = await page.evaluate(
        """() => {
            const out = {};
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                out[k] = localStorage.getItem(k);
            }
            return out;
        }"""
    )
    return {"cookies": cookies, "localStorage": storage}


async def register_one_account(
    mail_provider: str = "mailtm",
    headless: bool = False,
    register_9router: bool = False,
    nr_session: object = None,
    nr_base: str = "",
) -> GrokAccount:
    """Full registration flow for one Grok account. Returns GrokAccount.

    If register_9router, triggers 9Router device-code and authorizes in the
    same browser session (user is already logged in after signup).

    Raises RuntimeError on any step failure.
    """
    log("─" * 60)
    log("🚀 Starting Grok account registration")

    # Step 1: temp email
    mail = create_temp_mail(provider=mail_provider)
    mailbox = mail.generate()
    email = mailbox["address"]
    jwt = mailbox["jwt"]
    log_ok(f"   ✅ Temp email: {email}")

    # Step 2: launch browser
    async with launch_browser(headless=headless) as browser:
        page = await browser.new_page()
        await _block_oneTrust(page)

        await page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)
        log("   ✅ Landed on signup")

        # Step 3: email path
        await _click_signup_with_email(page)
        await asyncio.sleep(3)
        await _fill_email_and_submit(page, email)

        # Step 4: OTP screen
        await _wait_for_otp_screen(page)
        log("   ✅ OTP screen reached")

        # Step 5: poll email for OTP
        otp = await _poll_for_otp(mail, email, jwt, timeout=120)

        # Step 6: enter OTP + confirm
        await _enter_otp_and_confirm(page, otp)
        await asyncio.sleep(5)

        # Step 7: try forgot-password to set a real password.
        # If forgot-flow fails (Grok sometimes rate-limits), fall back to
        # relying on the signup session — the account exists + email is verified.
        try:
            password = await _set_password_via_forgot_flow(page, email, mail, jwt)
        except Exception as e:
            log_err(f"   ⚠️ forgot-flow failed ({e}); account still verified via signup OTP")
            password = "(signup-only — no password set)"  # ponytail: may still work for 9Router via session

        # Step 8: capture session + optionally register to 9Router
        await asyncio.sleep(3)
        session = await _capture_session(page)
        current_url = page.url
        log_ok(f"   ✅ Session captured at {current_url}")

        status = "verified"
        if DASHBOARD_URL_HINT in current_url:
            status = "verified"
        elif password and not password.startswith("("):
            status = "password_set"
        else:
            status = "partial"

        # Step 9 (optional): register to 9Router grok-cli in the SAME browser session.
        # User is already logged in from signup — no login OTP needed.
        nr_registered = False
        if register_9router:
            nr_registered = await register_9router_in_session(
                page=page,
                email=email,
                password=password,
                nr_session=nr_session,
                nr_base=nr_base,
            )

        account = GrokAccount(
            email=email,
            password=password,
            mail_jwt=jwt,
            session_cookies=session,
            created_at=time.time(),
            status=status,
            notes=f"final_url={current_url}" + (";9router=ok" if nr_registered else ""),
        )

        log_ok(f"   ✅ Account created: {email} [{status}]" + (" + 9Router ✅" if nr_registered else ""))
        log("─" * 60)
        return account


def save_account(account: GrokAccount, out_dir: Path | None = None) -> Path:
    """Append account as JSON to accounts.json in out_dir."""
    out_dir = out_dir or Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / "accounts.json"
    accounts = []
    if f.exists():
        try:
            accounts = json.loads(f.read_text())
        except json.JSONDecodeError:
            accounts = []
    accounts.append(asdict(account))
    f.write_text(json.dumps(accounts, indent=2, default=str))
    return f


async def run(
    n: int = 1,
    headless: bool = False,
    out_dir: str = ".",
    mail_provider: str = "mailtm",
    register_9router: bool = False,
    nr_session: object = None,
    nr_base: str = "",
) -> list[GrokAccount]:
    """Register N accounts sequentially."""
    results: list[GrokAccount] = []
    for i in range(n):
        log(f"\n[{i+1}/{n}]")
        try:
            acct = await register_one_account(
                mail_provider=mail_provider,
                headless=headless,
                register_9router=register_9router,
                nr_session=nr_session,
                nr_base=nr_base,
            )
            results.append(acct)
            save_account(acct, Path(out_dir))
        except Exception as e:
            log_err(f"   ❌ Account {i+1} failed: {e}")
            # Save partial failure for post-mortem
            save_account(
                GrokAccount(
                    email="",
                    password="",
                    mail_jwt="",
                    session_cookies={},
                    created_at=time.time(),
                    status="failed",
                    notes=str(e),
                ),
                Path(out_dir),
            )
        # Cooldown between accounts (avoid velocity detection)
        if i < n - 1:
            log("   ⏸️  30s cooldown…")
            await asyncio.sleep(30)
    return results
