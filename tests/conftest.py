"""
Qoder Autopilot — Test Fixtures
=================================
Shared fixtures for pytest.
"""

import json

import pytest


@pytest.fixture
def sample_otp_html():
    """Sample email HTML with OTP code embedded."""
    return """
    <html>
    <body>
        <p>Your verification code is:</p>
        <span style="letter-spacing: 8px; font-size: 24px">482913</span>
        <p>This code expires in 10 minutes.</p>
    </body>
    </html>
    """


@pytest.fixture
def sample_otp_html_no_code():
    """Email HTML without any OTP code."""
    return """
    <html>
    <body>
        <p>Welcome to Qoder!</p>
        <p>Your account has been created.</p>
    </body>
    </html>
    """


@pytest.fixture
def temp_creds_file(tmp_path):
    """Temporary credentials JSON file."""
    creds = [
        {
            "email": "test@example.com",
            "password": "Test123!",
            "display_name": "Test User",
            "status": "success",
        }
    ]
    fpath = tmp_path / "test_creds.json"
    fpath.write_text(json.dumps(creds, indent=2))
    return fpath


@pytest.fixture
def device_token_body():
    """Sample device token response from Qoder API."""
    return {
        "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test_token",
        "refresh_token": "refresh_token_abc123",
        "user_id": "user_12345",
        "expires_at": "2026-07-16T00:00:00Z",
        "expires_in": 2592000,
    }


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set mock environment variables for testing."""
    monkeypatch.setenv("QODER_WORKER_URL", "https://test-worker.example.com")
    monkeypatch.setenv("QODER_AI_API_KEY", "***")
    monkeypatch.setenv("QODER_AI_BASE_URL", "https://test-ai.example.com/v1")
    monkeypatch.setenv("QODER_AI_MODEL", "test-model-v1")
    monkeypatch.setenv("QODER_CAPTCHA_TIMEOUT", "60")
    monkeypatch.setenv("QODER_OTP_TIMEOUT", "90")
    monkeypatch.setenv("QODER_NINEROUTER_DB", "/tmp/test.sqlite")
