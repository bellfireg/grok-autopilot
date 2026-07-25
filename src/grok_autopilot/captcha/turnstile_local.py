"""
Grok Autopilot — Self-hosted Turnstile Solver Client
=====================================================
Drop-in replacement for the 2captcha solve_turnstile() function.
Talks to our local solver.py FastAPI server instead of paying 2captcha.

Requires: SOLVER_URL env var (default: http://127.0.0.1:8888)

Usage:
    from .turnstile_local import solve_turnstile_local
    token = solve_turnstile_local(page.url, sitekey)
"""

import os
import time

import requests

from ..utils.logger import log, log_err, log_ok

SOLVER_URL = os.environ.get("TURNSTILE_SOLVER_URL", "http://127.0.0.1:8890")


def solve_turnstile_local(
    website_url: str,
    website_key: str,
    action: str | None = None,
    cdata: str | None = None,
    timeout: int = 60,
) -> str | None:
    """Solve Turnstile via our self-hosted solver.

    Drop-in replacement for solve_turnstile() — same signature minus api_key.
    Returns token string or None on failure.
    """
    try:
        r = requests.get(
            f"{SOLVER_URL}/solve",
            params={"url": website_url, "sitekey": website_key,
                    **({"action": action} if action else {}),
                    **({"cdata": cdata} if cdata else {})},
            timeout=timeout + 10,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        log_err(f"   solver request failed: {e}")
        return None

    token = data.get("value")
    elapsed = data.get("elapsed_time", 0)

    if token:
        log_ok(f"   ✅ Turnstile solved (local, {elapsed}s): {token[:40]}…")
        return token

    log_err(f"   solver returned no token ({elapsed}s): {data}")
    return None


if __name__ == "__main__":
    # Smoke test: expects solver running at SOLVER_URL
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://accounts.x.ai/"
    key = sys.argv[2] if len(sys.argv) > 2 else "0x4AAAAAAAyJK2FfyvayqHnv"
    print(f"Testing solver at {SOLVER_URL} with url={url} sitekey={key}")
    token = solve_turnstile_local(url, key)
    print(f"Result: {token}" if token else "FAILED")
