# Architecture

## Overview

Qoder Autopilot automates the full lifecycle of Qoder account registration:

```
┌─────────────────────────────────────────────────────────┐
│                      CLI / Library                       │
│                    (cli.py / __init__)                   │
└────────────────────────┬────────────────────────────────┘
                         │
                    ┌────▼────┐
                    │register │  ← Orchestrates the full flow
                    │  .py    │
                    └────┬────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   ┌────▼─────┐   ┌──────▼──────┐  ┌─────▼──────┐
   │  browser/ │   │   captcha/  │  │ temp_mail  │
   │ camoufox  │   │   solver    │  │  (HTTP)    │
   │ tiler     │   │ ai_vision   │  └────────────┘
   └───────────┘   │ opencv      │
                   │ slider      │
                   │ manual      │
                   └─────────────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
         ┌────▼────┐ ┌──▼────┐ ┌──▼──────────┐
         │  oauth  │ │  otp  │ │  ninerouter │
         │  (PKCE) │ │(email)│ │  (SQLite)   │
         └─────────┘ └───────┘ └─────────────┘
```

## Module Responsibilities

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `config.py` | ~210 | Pydantic Settings, env vars, defaults |
| `errors.py` | ~130 | Exception hierarchy (12 classes) |
| `logger.py` | ~90 | Colored ANSI logging, context tags |
| `identity.py` | ~60 | Random Indonesian identity (Faker id_ID) |
| `credentials.py` | ~50 | JSON credential storage |
| `otp.py` | ~50 | OTP extraction from email HTML |
| `temp_mail.py` | ~100 | Cloudflare Worker email client |
| `oauth.py` | ~150 | PKCE device authorization flow |
| `ninerouter.py` | ~100 | 9Router SQLite DB integration |
| `register.py` | ~370 | Full registration orchestration |
| `cli.py` | ~260 | CLI entry point, parallel runner |

### Sub-packages

| Package | Files | Responsibility |
|---------|-------|----------------|
| `captcha/` | 5 | Captcha solving strategies (AI, OpenCV, manual) |
| `browser/` | 2 | Camoufox launcher + macOS window tiling |

## Data Flow

### Registration Flow (register.py)

1. **Navigate** → Open Qoder signup or OAuth URL in Camoufox
2. **Fill Form** → Enter generated name, email, accept ToS
3. **Password** → Enter generated strong password
4. **Captcha** → Solve via CaptchaSolver (AI → OpenCV → Manual)
5. **OTP Wait** → Poll temp mail inbox for verification code
6. **Enter OTP** → Type 6-digit code into OTP inputs
7. **Verify** → Check for redirect / success indicator

### OAuth Flow (oauth.py)

```
initiate_device_flow()          poll_device_token()
        │                              │
        ▼                              ▼
  PKCE verifier ──► auth_url ──► user signs up ──► 200 + token
  + challenge        │                │
  + nonce            │                │
  + machine_id       │                │
                     ▼                ▼
               browser opens    poll every 2s
               user registers   until 200 OK
```

### Captcha Solving (captcha/)

```
CaptchaSolver.solve(page)
        │
        ▼
  ┌─ AI Vision? ──► ai_vision.detect_gap() ──► find coordinates
  │     │ no/fail
  │     ▼
  ├─ OpenCV? ────► opencv_detect.detect() ──► find coordinates
  │     │ no/fail
  │     ▼
  └─ Manual ─────► manual.solve() ──► pause + poll for success
                      │
                      ▼
                slider.drag() ──► human-like mouse movement
```

## Configuration

All config via `pydantic-settings` with `QODER_` env prefix:

```python
from qoder_autopilot.config import settings

settings.worker_url          # QODER_WORKER_URL
settings.ai_api_key          # QODER_AI_API_KEY (or SUMOPOD_API_KEY)
settings.has_ai_captcha      # computed: bool(settings.ai_api_key)
settings.has_ninerouter      # computed: os.path.exists(db)
settings.ninerouter_db_path  # computed: os.path.expanduser(db)
```

## Error Handling

Hierarchical exceptions — catch at the appropriate level:

```python
from qoder_autopilot.errors import (
    QoderAutopilotError,    # catch-all base
    TempMailError,          # email issues
    CaptchaError,           # captcha failed
    RegistrationError,      # signup failed
    OAuthError,             # PKCE/token issues
    NineRouterError,        # 9Router DB issues
)
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `camoufox[geoip]` | Anti-detect Firefox browser |
| `playwright` | Browser automation |
| `requests` | HTTP client |
| `pydantic-settings` | Configuration management |
| `faker` | Realistic identity generation |
| `python-dotenv` | .env file loading |
| `opencv-python-headless` | (optional) Captcha CV detection |
| `openai` | (optional) AI captcha solving |
