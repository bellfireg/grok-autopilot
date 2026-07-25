"""
Grok Autopilot — Temporary Email Client (Multi-Provider)
===========================================================
Supports multiple temp mail backends via provider pattern.

Providers:
    - **cloudflare**: Cloudflare Worker-based (default, no API key needed)
    - **moca**: Supabase-based API by Moca (requires x-api-key)

Usage:
    from grok_autopilot.temp_mail import create_temp_mail

    tm = create_temp_mail()          # auto-detect from config
    addr = tm.generate()             # {"address": "abc@domain.com", ...}
    msgs = tm.inbox(addr["address"]) # [{"id": "...", "subject": "..."}, ...]
    msg  = tm.message(msgs[0]["id"]) # {"html": "...", "text": "..."}

    # Or pick provider explicitly:
    tm = create_temp_mail(provider="moca")
"""

import secrets
import time
from abc import ABC, abstractmethod
from urllib.parse import quote as url_quote

import requests

from ..errors import TempMailError
from ..utils.logger import log, log_err, log_ok
from . import config

# ═══════════════════════════════════════════════════════════════════════════════
# BASE CLASS
# ═══════════════════════════════════════════════════════════════════════════════


class TempMailProvider(ABC):
    """Abstract base class for temp mail providers."""

    @abstractmethod
    def generate(self) -> dict:
        """Generate a new temporary email address.

        Returns:
            dict with at least 'address' key.

        Raises:
            TempMailError: On API or network failure.
        """
        ...

    @abstractmethod
    def inbox(self, address: str) -> list[dict]:
        """Fetch inbox messages for the given address.

        Returns:
            List of message dicts with at least 'id' key.
        """
        ...

    @abstractmethod
    def message(self, msg_id: str, address: str | None = None) -> dict | None:
        """Fetch a single message by ID.

        Args:
            msg_id: Message identifier.
            address: Optional address (for Bearer-auth providers like CF worker).

        Returns:
            Message dict with 'html' and/or 'text' keys, or None.
        """
        ...

    def wait_for_email(
        self,
        address: str,
        timeout: int = 180,
        interval: int = 5,
    ) -> list[dict]:
        """Poll inbox until at least one email arrives or timeout.

        Returns:
            List of messages if received, empty list on timeout.
        """
        log(f"   ⏳ Waiting for email at {address}...")
        start = time.time()
        while time.time() - start < timeout:
            try:
                msgs = self.inbox(address)
                if msgs:
                    log_ok(f"Got {len(msgs)} email(s)!")
                    return msgs
            except TempMailError as e:
                log(f"   ⚠️ Inbox error (will retry): {e}")
            time.sleep(interval)
        log_err("Timeout waiting for email")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER: CLOUDFLARE WORKER
# ═══════════════════════════════════════════════════════════════════════════════


class CloudflareProvider(TempMailProvider):
    """Cloudflare Worker-based temp mail (bluk-cf contract).

    Endpoints (all gated by /<API_SECRET>/api/...):
        POST /<secret>/api/new_address    → mint 16-char localpart @ DOMAIN
        GET  /<secret>/api/parsed_mails   → list messages (Bearer: <address>)
        GET  /<secret>/api/parsed_mail/<id> → message detail
        GET  /<secret>/api/domains        → list supported domains

    Config:
        WORKER_URL  — full https://<name>.<acct>.workers.dev (no trailing /)
        WORKER_SECRET — the API_SECRET path prefix (env CF_MAILBOX_SECRET)
    """

    def __init__(self, worker_url: str | None = None, worker_secret: str | None = None):
        import os
        self.url = (worker_url or config.WORKER_URL).rstrip("/")
        self.secret = worker_secret or os.environ.get("CF_MAILBOX_SECRET") or getattr(config, "WORKER_SECRET", "")
        if not self.secret:
            raise TempMailError(
                "CloudflareProvider missing WORKER_SECRET",
                "Set CF_MAILBOX_SECRET env or config.WORKER_SECRET",
            )
        self._session = requests.Session()  # connection pooling

    def _api(self, method: str, path: str, bearer: str | None = None, **kw) -> dict:
        url = f"{self.url}/{self.secret}/api/{path.lstrip('/')}"
        headers = {}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            r = self._session.request(method, url, headers=headers, timeout=20, **kw)
            if r.status_code >= 400:
                raise TempMailError(f"CF worker {method} {path} → {r.status_code}", r.text[:300])
            return r.json() if r.text else {}
        except requests.RequestException as e:
            raise TempMailError(f"CF worker request failed: {e}", str(e)) from e

    def generate(self) -> dict:
        for attempt in range(1, 4):
            try:
                d = self._api("POST", "new_address")
                address = d["address"]
                self._last_address = address  # store for message() Bearer
                log_ok(f"   ✅ CF mailbox minted: {address}")
                return {"address": address, "jwt": address, "domain": d.get("domain", "")}
            except (TempMailError, KeyError) as e:
                if attempt < 3:
                    log(f"   ⚠️ mint attempt {attempt}/3 failed: {e}")
                    time.sleep(2)
                else:
                    raise
        raise TempMailError("CF mailbox mint failed after 3 attempts", "")

    def inbox(self, address: str) -> list[dict]:
        d = self._api("GET", "parsed_mails", bearer=address)
        # Response shape: bare list [] when empty, or list of dicts when populated
        rows = d if isinstance(d, list) else (
            d.get("data", {}).get("rows", []) if isinstance(d.get("data"), dict)
            else d.get("data", [])
        )
        return [
            {"id": m.get("id") or m.get("message_id") or m.get("_id", ""),
             "subject": m.get("subject", ""),
             "from": m.get("from", "")}
            for m in rows
        ]

    def message(self, msg_id: str, address: str | None = None) -> dict | None:
        # ponytail: address required for Bearer auth — caller must pass it.
        # We can't get it from msg_id alone. If not passed, try stored jwt.
        bearer = address or getattr(self, "_last_address", "")
        if not bearer:
            raise TempMailError(
                "CF message() needs address for Bearer auth",
                "Pass address= from generate() return",
            )
        d = self._api("GET", f"parsed_mail/{msg_id}", bearer=bearer)
        m = d.get("data", d)
        return {
            "html": m.get("html", "") if isinstance(m.get("html"), str) else (m.get("html", [""]) or [""])[0],
            "text": m.get("text", ""),
            "subject": m.get("subject", ""),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER: MOCA SUPABASE
# ═══════════════════════════════════════════════════════════════════════════════


class MocaProvider(TempMailProvider):
    """Supabase-based temp mail API by Moca (requires API key).

    Auth: x-api-key header (format: tmk_ + 64 hex chars)
    Base: https://ijrccpgiulrmfpavazsl.supabase.co/functions/v1/temp-mail-api

    Endpoints:
        GET  ?action=domains                          → list domains
        POST ?action=create      {desired_local, ...} → create inbox
        GET  ?action=messages    &address=&owner_token= → list messages
        GET  ?action=message     &id=&owner_token=     → message detail
        POST ?action=delete      {address, owner_token} → delete inbox
    """

    DEFAULT_BASE = "https://ijrccpgiulrmfpavazsl.supabase.co/functions/v1/temp-mail-api"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.api_key = api_key or config.MOCA_API_KEY
        if not self.api_key:
            raise TempMailError(
                "Moca provider requires API key",
                "Get one from @rubuskap on Telegram, then: "
                "grok-autopilot config set moca-api-key tmk_xxx",
            )
        self.base = (base_url or config.MOCA_BASE_URL).rstrip("/")
        self._owner_token: str | None = None
        self._session = requests.Session()  # R4: connection pooling

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "Content-Type": "application/json"}

    def _request(self, method: str, action: str, **kwargs) -> dict:
        """Make an API request."""
        url = f"{self.base}?action={action}"
        for k, v in kwargs.get("params", {}).items():
            url += f"&{k}={url_quote(str(v))}"

        try:
            r = self._session.request(
                method,
                url,
                headers=self._headers(),
                json=kwargs.get("json"),
                timeout=15,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            raise TempMailError(f"Request failed ({action})", str(e)) from e

        return r.json()

    def generate(self) -> dict:
        """Create a new inbox. Stores owner_token for subsequent calls."""
        body = {}
        # Optional: reuse owner_token to group inboxes
        if self._owner_token:
            body["owner_token"] = self._owner_token

        d = self._request("POST", "create", json=body)

        if "error" in d:
            raise TempMailError("Create inbox failed", d["error"])

        # Store owner_token for inbox/message calls
        self._owner_token = d.get("owner_token")

        return {
            "address": d["address"],
            "owner_token": d.get("owner_token", ""),
            "domain": d.get("domain", ""),
        }

    def inbox(self, address: str) -> list[dict]:
        if not self._owner_token:
            raise TempMailError(
                "No owner_token",
                "Call generate() first, or set owner_token manually",
            )

        d = self._request(
            "GET",
            "messages",
            params={"address": address, "owner_token": self._owner_token},
        )

        if "error" in d:
            raise TempMailError("Inbox fetch failed", d["error"])

        return d.get("messages", [])

    def message(self, msg_id: str, address: str | None = None) -> dict | None:
        if not self._owner_token:
            raise TempMailError("No owner_token", "Call generate() first")

        d = self._request(
            "GET",
            "message",
            params={"id": msg_id, "owner_token": self._owner_token},
        )

        if "error" in d:
            return None

        return d  # Full message object with html/text

    def delete_inbox(self, address: str) -> bool:
        """Delete an inbox and all its messages."""
        if not self._owner_token:
            return False

        d = self._request(
            "POST",
            "delete",
            json={"address": address, "owner_token": self._owner_token},
        )
        return d.get("ok", False)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIL.TM PROVIDER (public, no auth, no deploy)
# ═══════════════════════════════════════════════════════════════════════════════


class MailTmProvider(TempMailProvider):
    """mail.tm — public temp mail, no API key required.

    API: https://api.mail.tm
    Domains are dynamic (fetched at runtime). Account lives until deleted or TTL.
    JWT is stored internally after generate() — callers use base API.
    """

    BASE = "https://api.mail.tm"
    PASSWORD = "TempMail1234!"  # generic temp-mail password (mail.tm doesn't verify)

    def __init__(self) -> None:
        self._domain: str | None = None
        self._jwt: str | None = None  # set by generate(), used by inbox/message

    def _fetch_domain(self) -> str:
        if self._domain:
            return self._domain
        d = self._request("GET", f"{self.BASE}/domains", json=None, raw_path=True)
        members = d.get("hydra:member", [])
        if not members:
            raise TempMailError("mail.tm has no active domain", "Try again later")
        self._domain = members[0]["domain"]
        assert self._domain, "mail.tm returned empty domain"
        return self._domain

    def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        raw_path: bool = False,
        token: str | None = None,
    ) -> dict:
        url = path if raw_path else f"{self.BASE}{path}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            r = requests.request(method, url, json=json, headers=headers, timeout=20)
            if r.status_code >= 400:
                raise TempMailError(
                    f"mail.tm {method} {path} → {r.status_code}",
                    r.text[:300],
                )
            return r.json() if r.text else {}
        except requests.RequestException as e:
            raise TempMailError(f"mail.tm request failed: {e}", str(e)) from e

    def generate(self) -> dict[str, str]:
        domain = self._fetch_domain()
        local = f"grok_{secrets.token_hex(6)}"
        address = f"{local}@{domain}"
        self._request(
            "POST",
            f"{self.BASE}/accounts",
            json={"address": address, "password": self.PASSWORD},
            raw_path=True,
        )
        # Login to get JWT (stored internally for inbox/message)
        tok = self._request(
            "POST",
            f"{self.BASE}/token",
            json={"address": address, "password": self.PASSWORD},
            raw_path=True,
        )
        self._jwt = tok["token"]
        return {
            "address": address,
            "jwt": self._jwt,  # also expose in dict for callers who want it
            "domain": domain,
        }

    def inbox(self, address: str) -> list[dict]:
        if not self._jwt:
            raise TempMailError("mail.tm: call generate() first", "No JWT stored")
        d = self._request(
            "GET",
            f"{self.BASE}/messages",
            raw_path=True,
            token=self._jwt,
        )
        return [
            {"id": m["id"], "subject": m.get("subject", ""), "from": m.get("from", {})}
            for m in d.get("hydra:member", [])
        ]

    def message(self, msg_id: str, address: str | None = None) -> dict[str, str]:
        if not self._jwt:
            raise TempMailError("mail.tm: call generate() first", "No JWT stored")
        d = self._request(
            "GET",
            f"{self.BASE}/messages/{msg_id}",
            raw_path=True,
            token=self._jwt,
        )
        return {
            "html": d.get("html", [""])[0] if isinstance(d.get("html"), list) else d.get("html", ""),
            "text": d.get("text", ""),
            "subject": d.get("subject", ""),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════════════════════════


def create_temp_mail(provider: str | None = None) -> TempMailProvider:
    """Create a temp mail provider instance.

    Auto-detects provider from config if not specified:
        - If MOCA_API_KEY is set → MocaProvider
        - Otherwise → MailTmProvider (default, zero-setup)

    Args:
        provider: "mailtm", "cloudflare", or "moca". None = auto-detect.

    Returns:
        TempMailProvider instance.
    """
    if provider is None:
        provider = config.MAIL_PROVIDER

    if provider == "moca":
        return MocaProvider()
    elif provider == "cloudflare":
        return CloudflareProvider()
    elif provider in ("mailtm", "mail.tm", "mail_tm"):
        return MailTmProvider()
    else:
        raise TempMailError(
            f"Unknown provider: {provider}",
            "Available: mailtm (default), cloudflare, moca",
        )


# Backward compat: TempMail() returns the default provider
def TempMail() -> TempMailProvider:  # noqa: N802
    """Backward-compatible alias for create_temp_mail()."""
    return create_temp_mail()
