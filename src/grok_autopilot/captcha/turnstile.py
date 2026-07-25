"""
Grok Autopilot — 2captcha Turnstile Solver
============================================
Solves Cloudflare Turnstile challenges via 2captcha v1 API.

Our key only supports v1 (in.php/res.php), not v2 (createTask).
Method: turnstile
Cost: ~$1.45/1000 solves
"""

import time

import requests

from ..utils.logger import log, log_err, log_ok

# v1 API endpoints
IN_PHP = "https://2captcha.com/in.php"
RES_PHP = "https://2captcha.com/res.php"


def solve_turnstile(
    api_key: str,
    website_url: str,
    website_key: str,
    action: str | None = None,
    cdata: str | None = None,
    timeout: int = 180,
) -> str | None:
    """Solve a Cloudflare Turnstile challenge via 2captcha v1 API.

    Args:
        api_key: 2captcha API key (v1 compatible).
        website_url: Full URL of the page with Turnstile.
        website_key: The sitekey (data-sitekey attribute or from iframe src).
        action: Optional action (data-action).
        cdata: Optional cdata (data-cdata).
        timeout: Max seconds to wait for solution.

    Returns:
        Turnstile token (cf-turnstile-response), or None on failure.
    """
    # Submit task via in.php
    params: dict = {
        "key": api_key,
        "method": "turnstile",
        "sitekey": website_key,
        "pageurl": website_url,
        "json": 1,
    }
    if action:
        params["action"] = action
    if cdata:
        params["data"] = cdata

    try:
        r = requests.post(IN_PHP, data=params, timeout=30).json()
    except requests.RequestException as e:
        log_err(f"   2captcha in.php request failed: {e}")
        return None

    if r.get("status") != 1:
        log_err(f"   2captcha in.php: {r.get('request', r)}")
        return None

    captcha_id = r["request"]
    log(f"   🔄 2captcha solving Turnstile (id={captcha_id})…")

    # Poll res.php for result
    deadline = time.time() + timeout
    time.sleep(10)  # initial wait — Turnstile takes longer than image captcha
    while time.time() < deadline:
        try:
            g = requests.get(
                RES_PHP,
                params={"key": api_key, "action": "get", "id": captcha_id, "json": 1},
                timeout=15,
            ).json()
        except requests.RequestException as e:
            log_err(f"   2captcha poll err: {e}")
            time.sleep(5)
            continue

        if g.get("status") == 1:
            token = g.get("request", "")
            if token and token != "CAPCHA_NOT_READY":
                log_ok(f"   ✅ Turnstile solved: {token[:40]}…")
                return token
        elif g.get("request") == "CAPCHA_NOT_READY":
            time.sleep(5)
            continue
        else:
            log_err(f"   2captcha res.php: {g.get('request', g)}")
            return None

    log_err(f"   2captcha timeout ({timeout}s)")
    return None


async def extract_turnstile_sitekey(page) -> str | None:
    """Extract Turnstile sitekey from window.__captcha_data (set by interceptor).

    Per 2captcha docs: sitekey is in `b.sitekey` of `turnstile.render(a, b)` call.
    We monkey-patch render BEFORE page load to capture it.
    """
    try:
        data = await page.evaluate(
            "() => window.__captcha_data ? JSON.stringify(window.__captcha_data) : null"
        )
        if data:
            import json
            d = json.loads(data)
            if d.get("sitekey"):
                return d["sitekey"]
        return None
    except Exception:
        return None


INTERCEPT_SCRIPT = """
(() => {
    if (window.__captcha_intercept_installed) return;
    window.__captcha_intercept_installed = true;
    window.__captcha_data = null;
    window.__captcha_callback = null;

    const _patchTurnstile = () => {
        if (window.turnstile && !window.turnstile._patched) {
            // Patch render()
            if (typeof window.turnstile.render === 'function') {
                const _origRender = window.turnstile.render;
                window.turnstile.render = function(container, params) {
                    params = params || {};
                    window.__captcha_data = {
                        sitekey: params.sitekey,
                        action: params.action || null,
                        cData: params.cData || null,
                        hasCallback: !!params.callback,
                        container: typeof container === 'string' ? container : 'element',
                        method: 'render'
                    };
                    if (typeof params.callback === 'function') {
                        window.__captcha_callback = params.callback;
                    }
                    console.log('[CAPTCHA] intercepted turnstile.render:', JSON.stringify(window.__captcha_data));
                    return _origRender.apply(this, arguments);
                };
            }
            // Patch execute() — xAI uses this for invisible Turnstile
            if (typeof window.turnstile.execute === 'function') {
                const _origExecute = window.turnstile.execute;
                window.turnstile.execute = function(container, params) {
                    params = params || {};
                    // Try to get sitekey from params or widget config
                    const sitekey = params.sitekey || (typeof container === 'string' ? document.querySelector('#' + container)?.getAttribute('data-sitekey') : null);
                    window.__captcha_data = {
                        sitekey: sitekey,
                        action: params.action || null,
                        cData: params.cData || null,
                        hasCallback: !!params.callback,
                        container: typeof container === 'string' ? container : 'element',
                        method: 'execute'
                    };
                    if (typeof params.callback === 'function') {
                        window.__captcha_callback = params.callback;
                    }
                    console.log('[CAPTCHA] intercepted turnstile.execute:', JSON.stringify(window.__captcha_data));
                    return _origExecute.apply(this, arguments);
                };
            }
            // Patch getResponse() — some implementations use this
            if (typeof window.turnstile.getResponse === 'function') {
                const _origGet = window.turnstile.getResponse;
                window.turnstile.getResponse = function() {
                    const r = _origGet.apply(this, arguments);
                    if (r && !window.__captcha_data) {
                        window.__captcha_data = { sitekey: null, token: r, method: 'getResponse' };
                    }
                    return r;
                };
            }
            window.turnstile._patched = true;
            console.log('[CAPTCHA] turnstile patched (render+execute+getResponse)');
        } else {
            setTimeout(_patchTurnstile, 30);
        }
    };
    _patchTurnstile();

    // Also intercept fetch/XHR to capture Turnstile sitekey from network
    const _origFetch = window.fetch;
    window.fetch = function() {
        const url = arguments[0]?.url || arguments[0] || '';
        if (typeof url === 'string' && url.includes('turnstile')) {
            console.log('[CAPTCHA] fetch to turnstile:', url.substring(0, 100));
        }
        return _origFetch.apply(this, arguments);
    };
})();
"""


async def inject_turnstile_token(page, token: str) -> bool:
    """Inject Turnstile token — create hidden input if none exists."""
    try:
        result = await page.evaluate(
            """(token) => {
                const log = [];
                // 1) Call captured callback
                if (typeof window.__captcha_callback === 'function') {
                    try { window.__captcha_callback(token); log.push('callback-called'); } catch(e) { log.push('callback-err:' + e.message); }
                }
                // 2) Set existing hidden input
                let inp = document.querySelector('input[name="cf-turnstile-response"], input[name="cf_challenge_response"]');
                // 3) Create hidden input if none exists — React form checks for this
                if (!inp) {
                    inp = document.createElement('input');
                    inp.type = 'hidden';
                    inp.name = 'cf-turnstile-response';
                    // Append to form if exists, else body
                    const form = document.querySelector('form');
                    if (form) form.appendChild(inp);
                    else document.body.appendChild(inp);
                    log.push('created-input');
                }
                inp.value = token;
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                log.push('hidden-input-set');
                // 4) Try turnstile callback API directly
                if (typeof window.turnstile !== 'undefined' && typeof window.turnstile.successCallback === 'function') {
                    try { window.turnstile.successCallback(token); log.push('turnstile-cb'); } catch(e) {}
                }
                return log;
            }""",
            token,
        )
        log(f"   🔧 Turnstile token injected: {result}")
        return True
    except Exception as e:
        log_err(f"   Turnstile injection failed: {e}")
        return False