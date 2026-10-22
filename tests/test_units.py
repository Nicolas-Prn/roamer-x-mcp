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
