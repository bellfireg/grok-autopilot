"""Self-check tests for Grok OTP extraction (alphanumeric subject format)."""
import asyncio
import sys
from pathlib import Path

# Add src to path for direct test run
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from grok_autopilot.register import _extract_otp_from_email  # noqa: E402


class TestOtpExtraction:
    def test_subject_alphanumeric_hyphen(self):
        """Real Grok subject format: 'SpaceXAI confirmation code: S23-XSW'."""
        otp = asyncio.run(_extract_otp_from_email("", subject="SpaceXAI confirmation code: S23-XSW"))
        assert otp == "S23-XSW", f"got {otp}"

    def test_subject_alphanumeric_no_code_keyword(self):
        """Subject without 'code' keyword still matches XXX-XXX pattern."""
        otp = asyncio.run(_extract_otp_from_email("", subject="Your verification: AB-CD12"))
        assert otp == "AB-CD12"

    def test_subject_six_digit(self):
        """Numeric 6-digit OTP in subject."""
        otp = asyncio.run(_extract_otp_from_email("", subject="Your code is 482910"))
        assert otp == "482910"

    def test_body_alphanumeric(self):
        """Alphanumeric OTP in body text."""
        otp = asyncio.run(_extract_otp_from_email("Please enter XY-9K2 to verify", subject=""))
        assert otp == "XY-9K2"

    def test_body_six_digit_near_code_keyword(self):
        """Numeric OTP in body near 'code' keyword."""
        otp = asyncio.run(_extract_otp_from_email("Your code: 123456\nthanks", subject=""))
        assert otp == "123456"

    def test_skip_placeholder_333333(self):
        """Grok email template has 333333 placeholder — must skip it."""
        otp = asyncio.run(
            _extract_otp_from_email(
                "Enter 333333 as example\nYour real code: 999111", subject=""
            )
        )
        assert otp == "999111"

    def test_no_otp_returns_none(self):
        otp = asyncio.run(_extract_otp_from_email("nothing here", subject="hello world"))
        assert otp is None

    def test_real_grok_subject_from_log(self):
        """Subject observed in production: 'SpaceXAI confirmation code: S23-XSW'."""
        otp = asyncio.run(
            _extract_otp_from_email("irrelevant body", subject="SpaceXAI confirmation code: S23-XSW")
        )
        assert otp == "S23-XSW"

    def test_otp_clean_for_form(self):
        """Form pattern ^[a-zA-Z0-9]+$ — hyphen must be stripped, uppercased."""
        import re
        otp = "S23-XSW"
        clean = re.sub(r"[^a-zA-Z0-9]", "", otp).upper()[:6]
        assert clean == "S23XSW"
        assert len(clean) == 6


if __name__ == "__main__":
    t = TestOtpExtraction()
    for name in dir(t):
        if name.startswith("test_"):
            getattr(t, name)()
            print(f"  ✓ {name}")
    print("All OTP extraction tests passed.")

