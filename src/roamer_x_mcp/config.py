"""Settings storage: IBSS tenant + the user's bearer token, shared by every AI client on the machine.

Everything lives in `~/.config/roamer-x-mcp/` (override with `ROAMER_MCP_HOME`): `config.json`
(tenant + current token, mode 600) and `browser/` (the browser profile holding the Microsoft session,
see `auth.py`).

`ROAMER_TENANT` / `ROAMER_TOKEN` environment variables take precedence over `config.json`.

The tenant is the IBSS environment name found in the web hub URL: `hub.ibss.<tenant>.iconics.cloud`.
ULB/VUB (LIC building) use `uob`.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TENANT = "uob"

HOME = Path(os.environ.get("ROAMER_MCP_HOME") or Path.home() / ".config" / "roamer-x-mcp")
CONFIG_FILE = HOME / "config.json"


class NotConfiguredError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    tenant: str
    token: str

    @property
    def api_url(self) -> str:
        return f"https://api.ibss.{self.tenant}.iconics.cloud"

    @property
    def hub_url(self) -> str:
        return f"https://hub.ibss.{self.tenant}.iconics.cloud"


def normalize_tenant(value: str) -> str:
    """Accept a bare tenant (`uob`) or any hub/API URL of that tenant."""
    value = value.strip().lower()
    m = re.search(r"ibss\.([a-z0-9-]+)\.iconics\.cloud", value)
    tenant = m.group(1) if m else value
    if not re.fullmatch(r"[a-z0-9-]+", tenant):
        raise ValueError(f"not a valid IBSS tenant: {value!r}")
    return tenant


def parse_token(text: str) -> str:
    """Extract the JWT from what the user pasted (raw token, or an `Authorization: Bearer …` header)."""
    text = text.strip()
    text = re.sub(r"^(authorization\s*:\s*)?bearer\s+", "", text, flags=re.I).strip()
    if not re.fullmatch(r"[\w-]+\.[\w-]+\.[\w-]*", text):
        raise ValueError("this does not look like a JWT (three dot-separated base64url parts)")
    return text


def load(require_token: bool = True) -> Settings:
    stored = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    tenant = os.environ.get("ROAMER_TENANT") or stored.get("tenant") or DEFAULT_TENANT
    token = os.environ.get("ROAMER_TOKEN") or stored.get("token") or ""
    if require_token and not token:
        raise NotConfiguredError(
            "Not signed in to Roamer X. Run `roamer-x-mcp login` in a terminal (see the README)."
        )
    return Settings(normalize_tenant(tenant), token)


def save(settings: Settings) -> None:
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"tenant": settings.tenant, "token": settings.token}, f, indent=1)
    os.replace(tmp, CONFIG_FILE)
