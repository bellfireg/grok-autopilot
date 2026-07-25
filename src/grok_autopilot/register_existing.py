"""Backfill existing password_set accounts into 9Router.

Reuses ninerouter_grok.register_account_to_ninerouter (login + OTP + Allow + poll).
Saves accounts.json after each success.

Usage:
    python -m grok_autopilot.register_existing --limit 5
    python -m grok_autopilot.register_existing   # all password_set missing 9R
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grok_autopilot.ninerouter_grok import ninerouter_login, register_account_to_ninerouter
from grok_autopilot.utils.logger import log, log_err, log_ok


def _save(path: str, accounts: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(accounts, f, indent=2, default=str)
    Path(tmp).replace(path)


async def run(accounts_path: str, limit: int = 0, headless: bool = True) -> tuple[int, int]:
    with open(accounts_path) as f:
        accounts = json.load(f)

    targets = [
        (i, a)
        for i, a in enumerate(accounts)
        if a.get("status") == "password_set"
        and "9router=ok" not in (a.get("notes") or "")
        and (a.get("email") or "").strip()
        and (a.get("password") or "").strip()
        and not (a.get("password") or "").startswith("(")
    ]
    if limit > 0:
        targets = targets[:limit]

    log(f"Backfill targets: {len(targets)}")
    if not targets:
        return 0, 0

    nr = ninerouter_login()
    mailbox_secret = os.environ.get("CF_MAILBOX_SECRET", "")
    worker_url = os.environ.get("WORKER_URL", "")

    ok = fail = 0
    for n, (i, acct) in enumerate(targets, 1):
        email = acct.get("email", "")
        log(f"\n[{n}/{len(targets)}] idx={i} {email[:40]}")
        try:
            if await register_account_to_ninerouter(
                acct,
                nr=nr,
                headless=headless,
                mailbox_secret=mailbox_secret or None,
                worker_url=worker_url or None,
            ):
                notes = acct.get("notes") or ""
                if "9router=ok" not in notes:
                    acct["notes"] = (notes + ";9router=ok").lstrip(";")
                ok += 1
                _save(accounts_path, accounts)
                log_ok(f"   progress {ok} ok / {fail} fail")
            else:
                fail += 1
        except Exception as e:
            fail += 1
            log_err(f"   ❌ {e}")
        if n < len(targets):
            await asyncio.sleep(8)

    _save(accounts_path, accounts)
    log_ok(f"\nDONE backfill: {ok} ok, {fail} fail, targets={len(targets)}")
    return ok, len(targets)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--accounts", default="./accounts/accounts.json")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--headed", action="store_true", help="Show browser")
    args = p.parse_args()
    ok, total = asyncio.run(run(args.accounts, limit=args.limit, headless=not args.headed))
    return 0 if ok > 0 or total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
