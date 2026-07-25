"""
Grok Autopilot
================
Automated Grok (accounts.x.ai) account registration with anti-detect
browser (Camoufox) and mail.tm temp mail. Zero captcha in email path.

Quick start::

    from grok_autopilot import run
    import asyncio

    asyncio.run(run(n=1))
"""

__version__ = "0.6.3"

from .cli import main
from .infra.temp_mail import TempMail, create_temp_mail
from .register import run, register_one_account

__all__ = [
    "run",
    "register_one_account",
    "TempMail",
    "create_temp_mail",
    "main",
]
