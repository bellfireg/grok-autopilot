"""
Grok Autopilot — CLI Entry Point
==================================
Usage:
    grok-autopilot -n 3
    grok-autopilot -n 1 --register-9router --headless
"""

import argparse
import asyncio
import os as _os
import sys

from .register import run
from .utils.logger import log, set_log_file


def main() -> int:
    p = argparse.ArgumentParser(
        prog="grok-autopilot",
        description="Automated Grok (accounts.x.ai) account registration",
    )
    p.add_argument("-n", "--count", type=int, default=1, help="Number of accounts to create")
    p.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (default: visible)",
    )
    p.add_argument(
        "--mail",
        default="mailtm",
        choices=["mailtm", "cloudflare", "moca"],
        help="Temp mail provider (default: mailtm)",
    )
    p.add_argument(
        "--out",
        default="./accounts",
        help="Output directory for accounts.json",
    )
    p.add_argument(
        "--register-9router",
        action="store_true",
        help="Register each account to 9Router grok-cli IN the signup browser (E2E 1-browser)",
    )
    p.add_argument("--log-file", default=None, help="Write logs to file")
    args = p.parse_args()

    if args.log_file:
        set_log_file(args.log_file)

    log(f"Grok Autopilot — {args.count} account(s) via {args.mail}")

    # If --register-9router, login to 9Router BEFORE signup (for device-code trigger)
    nr_session = None
    nr_base = ""
    if args.register_9router:
        nr_base = _os.environ.get(
            "NINEROUTER_HOST", "http://localhost:20128"
        ).rstrip("/")
        nr_password = _os.environ.get("NINEROUTER_PASSWORD", "")
        if not nr_password:
            log("❌ NINEROUTER_PASSWORD env var required for --register-9router")
            return 1
        try:
            import requests as _req
            r = _req.post(
                f"{nr_base}/api/auth/login",
                json={"password": nr_password},
                timeout=15,
            )
            r.raise_for_status()
            nr_session = _req.Session()
            for k, v in r.cookies.items():
                nr_session.cookies.set(k, v) if v else None
            log(f"✅ 9Router login OK")
        except Exception as e:
            log(f"❌ 9Router login failed: {e}")
            return 1

    accounts = asyncio.run(
        run(
            n=args.count,
            headless=args.headless,
            out_dir=args.out,
            mail_provider=args.mail,
            register_9router=args.register_9router,
            nr_session=nr_session,
            nr_base=nr_base,
        )
    )

    ok = sum(1 for a in accounts if a.status in ("verified", "password_set"))
    nr_ok = sum(1 for a in accounts if "9router=ok" in a.notes)
    log(f"\n{'='*60}")
    log(f"DONE: {ok}/{args.count} accounts created, {nr_ok} to 9Router")

    for a in accounts:
        nr_tag = " +9Router" if "9router=ok" in a.notes else ""
        log(f"  - {a.email} [{a.status}]{nr_tag}")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
