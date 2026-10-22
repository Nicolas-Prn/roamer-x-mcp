"""Minimal ICONICS IBSS API client (JSON over HTTPS, stdlib only).

Endpoints are documented by the tenant's public Swagger (`<api_url>/swagger/index.html`); a snapshot of
both API versions is kept in `docs/spec/`.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import __version__, auth, config
from .config import Settings

USER_AGENT = f"roamer-x-mcp/{__version__}"


def _error_detail(body: str) -> str:
    """Keep the human-readable reason from an IBSS error payload (`Errors[].Detail`, `Detail`, `Title`)."""
    try:
        data = json.loads(body)
    except ValueError:
        return body[:500]
    if not isinstance(data, dict):
        return body[:500]
    reasons = [err.get("Detail") for err in data.get("Errors") or [] if isinstance(err, dict) and err.get("Detail")]
    reasons += [data[k] for k in ("Detail", "Title") if data.get(k)]
    return " | ".join(dict.fromkeys(reasons)) or body[:500]


class IbssError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Ibss:
    """API client; renews the token through `auth` when it is about to expire or is rejected.

    `auto_refresh=False` keeps the given token as is (used to validate a token right after login).
    """

    def __init__(self, settings: Settings, auto_refresh: bool = True):
        self.url = settings.api_url
        self._token = settings.token
        self._auto_refresh = auto_refresh

    @property
    def token(self) -> str:
        return self._token

    def _refresh(self) -> None:
        auth.refresh_in_subprocess()
        self._token = config.load().token

    def _scrub(self, text: str) -> str:
        # Error messages end up in the model's context; never let the token leak there.
        return text.replace(self._token, "<token>")

    def request(self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None) -> Any:
        if self._auto_refresh and not auth.is_fresh(self._token):
            self._refresh()
        try:
            return self._send(method, path, params, body)
        except IbssError as e:
            if not (self._auto_refresh and e.status == 401):
                raise
        self._refresh()
        return self._send(method, path, params, body)

    def _send(self, method: str, path: str, params: dict[str, Any] | None, body: Any) -> Any:
        url = f"{self.url}/{path.lstrip('/')}"
        if params:
            query = {k: v for k, v in params.items() if v is not None}
            if query:
                url += "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = _error_detail(e.read().decode(errors="replace"))
            hint = " (token expired or invalid: run `roamer-x-mcp login`)" if e.code == 401 else ""
            raise IbssError(self._scrub(f"{method} {path}: HTTP {e.code}{hint}: {detail}"), e.code) from None
        except (TimeoutError, OSError) as e:
            raise IbssError(self._scrub(f"{method} {path}: cannot reach {self.url} ({e})")) from None
        return json.loads(raw) if raw else None

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params)

    def post(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("POST", path, params, body)

    def patch(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("PATCH", path, params, body)

    def get_all(self, path: str, limit: int = 500, **params: Any) -> list[dict]:
        """Follow OData pagination (v2 list endpoints) up to `limit` items."""
        page = self.get(path, **params)
        if isinstance(page, list):
            return page[:limit]
        items: list[dict] = list(page.get("Value", page.get("value", [])))
        while (next_link := page.get("@odata.nextLink")) and len(items) < limit:
            # The next link drops the original $filter (following it fails with HTTP 500): like the Hub, resend
            # the same query with only the skip token added.
            m = re.search(r"\$skiptoken=([^&]+)", next_link, re.I)
            if not m:
                break
            page = self.get(path, **params, **{"$skiptoken": urllib.parse.unquote(m.group(1))})
            items.extend(page.get("Value", page.get("value", [])))
        return items[:limit]
