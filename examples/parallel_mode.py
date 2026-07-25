"""
Qoder Autopilot — Parallel Mode Example
=========================================
Register multiple accounts concurrently with grid window tiling.

Prerequisites:
    pip install -e ".[all]"
    python -m camoufox fetch
    # Configure .env (see .env.example)
"""

import asyncio

from qoder_autopilot.cli import run_one, main_async
from qoder_autopilot.errors import QoderAutopilotError


async def parallel_example():
    """Register 4 accounts in parallel."""
    import argparse

    # Simulate CLI args for parallel mode
    args = argparse.Namespace(
        count=4,
        no_headless=False,
        no_oauth=False,
        manual_captcha=True,
        parallel=True,
        delay=30,
    )

    await main_async(args)


async def sequential_with_control():
    """Register accounts one by one with custom logic between each."""
    results = []

    for i in range(3):
        print(f"\n{'='*60}")
        print(f"📦 Account {i + 1}/3")
        print(f"{'='*60}")

        try:
            result = await run_one(
                headless=False,
                use_oauth=True,
                manual_captcha=True,
                acct_num=i + 1,
            )
            results.append(result)

            if result:
                print(f"   ✅ {result['email']}")
            else:
                print(f"   ❌ Failed")

        except QoderAutopilotError as e:
            print(f"   ❌ Error: {e}")
            results.append(None)

        # Wait between accounts
        if i < 2:
            print("\n⏳ Waiting 30s before next account...")
            await asyncio.sleep(30)

    # Summary
    success = sum(1 for r in results if r)
    print(f"\n{'='*60}")
    print(f"📊 Results: {success}/{len(results)} succeeded")
    print(f"{'='*60}")


if __name__ == "__main__":
    # Choose mode:
    # asyncio.run(parallel_example())     # 4 accounts at once
    asyncio.run(sequential_with_control())  # 3 accounts one by one
