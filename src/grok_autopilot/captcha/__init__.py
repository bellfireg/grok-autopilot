"""Captcha solving subpackage."""
from .turnstile import (
    solve_turnstile,
    extract_turnstile_sitekey,
    inject_turnstile_token,
    INTERCEPT_SCRIPT,
)
from .turnstile_local import solve_turnstile_local

__all__ = [
    "solve_turnstile",
    "solve_turnstile_local",
    "extract_turnstile_sitekey",
    "inject_turnstile_token",
    "INTERCEPT_SCRIPT",
]
