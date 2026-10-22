import pytest

from roamer_x_mcp import config


@pytest.mark.parametrize("value", ["uob", " UOB ", "https://hub.ibss.uob.iconics.cloud/", "api.ibss.uob.iconics.cloud"])
def test_normalize_tenant(value):
    assert config.normalize_tenant(value) == "uob"


def test_normalize_tenant_rejects_garbage():
    with pytest.raises(ValueError):
        config.normalize_tenant("not a tenant!")


@pytest.mark.parametrize("pasted", ["aaa.bbb.ccc", "Bearer aaa.bbb.ccc", "Authorization: Bearer aaa.bbb.ccc\n"])
def test_parse_token(pasted):
    assert config.parse_token(pasted) == "aaa.bbb.ccc"


def test_parse_token_rejects_non_jwt():
    with pytest.raises(ValueError):
        config.parse_token("hello")


def test_settings_urls():
    s = config.Settings("uob", "t")
    assert s.api_url == "https://api.ibss.uob.iconics.cloud"
    assert s.hub_url == "https://hub.ibss.uob.iconics.cloud"


def _jwt(payload: dict) -> str:
    import base64, json
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'none'})}.{enc(payload)}.sig"


def test_token_freshness():
    import time
    from roamer_x_mcp import auth
    assert auth.is_fresh(_jwt({"exp": time.time() + 3600}))
    assert not auth.is_fresh(_jwt({"exp": time.time() + 60}))
    assert not auth.is_fresh("garbage")
    assert not auth.is_fresh(None)


def test_error_detail_extracts_reason():
    import json
    from roamer_x_mcp.client import _error_detail
    body = json.dumps({"Errors": [{"Detail": "cannot be booked outside the office hours"}], "Title": "No permission",
                       "Detail": None, "Status": 403})
    assert _error_detail(body) == "cannot be booked outside the office hours | No permission"
    assert _error_detail("plain text") == "plain text"


def test_policy_check():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from roamer_x_mcp.booking import Policy, Roamer
    pol = Policy("Study Seat", "", 30, 240, 7, False, [], True)
    tz = ZoneInfo("Europe/Brussels")
    start = (datetime.now(tz) + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    r = Roamer(api=None)
    r.check_policy(pol, start, start + timedelta(hours=2))
    for end in (start + timedelta(hours=5), start + timedelta(minutes=15)):
        with pytest.raises(ValueError):
            r.check_policy(pol, start, end)
    with pytest.raises(ValueError, match="too far ahead"):
        r.check_policy(pol, start + timedelta(days=10), start + timedelta(days=10, hours=1))


def test_json_merge_add_and_remove(tmp_path):
    import json
    from roamer_x_mcp import cli
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    cli._json_merge(path, ["/bin/roamer-x-mcp", "serve"])
    data = json.loads(path.read_text())
    assert data["mcpServers"]["roamer-x"] == {"command": "/bin/roamer-x-mcp", "args": ["serve"]}
    assert "other" in data["mcpServers"]
    cli._json_merge(path, None)
    assert "roamer-x" not in json.loads(path.read_text())["mcpServers"]


def test_codex_table_is_replaced(tmp_path, monkeypatch):
    from roamer_x_mcp import cli
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text('[mcp_servers.moodle]\ncommand = "m"\n')
    cli._codex(["/a", "serve"])
    cli._codex(["/b", "serve"])
    text = (tmp_path / ".codex/config.toml").read_text()
    assert text.count('[mcp_servers."roamer-x"]') == 1 and '"/b"' in text and "[mcp_servers.moodle]" in text
