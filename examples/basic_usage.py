"""
Qoder Autopilot — Basic Usage Example
=======================================
Register a single Qoder account with manual captcha solving.

Prerequisites:
    pip install -e ".[all]"
    python -m camoufox fetch
    # Configure .env (see .env.example)
"""

import asyncio

from qoder_autopilot import run_one
from qoder_autopilot.errors import QoderAutopilotError


async def main():
    """Register a single account."""
    try:
        result = await run_one(
            headless=False,       # Show browser window
            use_oauth=True,       # Connect to 9Router
            manual_captcha=True,  # Pause for manual captcha
        )

        if result:
            print(f"\n✅ Success!")
            print(f"   Email: {result['email']}")
            if 'token' in result:
                print(f"   Token: {result['token'][:30]}...")
        else:
            print("\n❌ Registration failed")

    except QoderAutopilotError as e:
        print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
