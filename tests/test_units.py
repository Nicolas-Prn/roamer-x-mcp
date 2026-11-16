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


def test_refresh_skipped_when_another_process_already_refreshed(tmp_path, monkeypatch):
    import time
    from roamer_x_mcp import auth, config
    monkeypatch.setattr(auth, "REFRESH_LOCK", tmp_path / "refresh.lock")
    monkeypatch.setattr(config, "HOME", tmp_path)
    fresh = _jwt({"exp": time.time() + 3600})
    monkeypatch.setattr(config, "load", lambda require_token=True: config.Settings("uob", fresh))
    calls = []
    monkeypatch.setattr(auth.subprocess, "run", lambda *a, **k: calls.append(a))
    auth.refresh_in_subprocess(stale_token="old.expired.token")
    assert calls == []


def test_refresh_runs_when_stored_token_is_the_rejected_one(tmp_path, monkeypatch):
    import time, types
    from roamer_x_mcp import auth, config
    monkeypatch.setattr(auth, "REFRESH_LOCK", tmp_path / "refresh.lock")
    monkeypatch.setattr(config, "HOME", tmp_path)
    rejected = _jwt({"exp": time.time() + 3600})
    monkeypatch.setattr(config, "load", lambda require_token=True: config.Settings("uob", rejected))
    calls = []
    monkeypatch.setattr(auth.subprocess, "run",
                        lambda *a, **k: calls.append(a) or types.SimpleNamespace(returncode=0, stderr=""))
    auth.refresh_in_subprocess(stale_token=rejected)
    assert len(calls) == 1


def test_refresh_lock_is_exclusive(tmp_path, monkeypatch):
    import threading, time
    from roamer_x_mcp import auth, config
    monkeypatch.setattr(auth, "REFRESH_LOCK", tmp_path / "refresh.lock")
    monkeypatch.setattr(config, "HOME", tmp_path)
    inside, overlaps = [], []

    def worker():
        with auth.refresh_lock():
            overlaps.append(len(inside))
            inside.append(1)
            time.sleep(0.2)
            inside.pop()

    threads = [threading.Thread(target=worker) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert overlaps == [0, 0, 0]


def test_observe_parsing():
    from datetime import date
    from roamer_x_mcp import observe
    assert observe.parse_offsets("0,5-7") == [0, 5, 6, 7]
    assert observe.parse_slot("09:00-10:30") == ("09:00", "10:30")
    got = observe.targets(date(2026, 11, 16), [0, 1], [("09:00", "10:30")])
    assert [t.day.isoformat() for t in got] == ["2026-11-16", "2026-11-17"]
    with pytest.raises(ValueError):
        observe.parse_slot("9h-10h")


def test_observe_records_eligible_rooms_and_errors(tmp_path):
    import json
    from datetime import date, datetime, timezone
    from zoneinfo import ZoneInfo
    from roamer_x_mcp import observe
    from roamer_x_mcp.booking import Building, Policy, Roamer
    from roamer_x_mcp.client import IbssError

    student = Policy("Meeting Rooms", "up to 7 days", 15, 120, 7, False, [], True)
    staff = Policy("Office Policy", "STAFF ONLY", 15, 900, 120, True, [], True)

    class FakeRoamer(Roamer):
        def search(self, building, start, end, *args):
            if start.day == 18:
                raise IbssError("GET v2/36/spaces/search: HTTP 401: expired", 401)
            return [
                {"Space_Id": "LIC.2.06", "Space_Name": "LIC.2.06 - Casablanca", "Space_Capacity": 4,
                 "Node_Id": 1, "Booking_Policy_Id": "student"},
                {"Space_Id": "LIC.4.17", "Space_Name": "LIC.4.17 - Stellenbosch - STAFF ONLY", "Space_Capacity": 5,
                 "Node_Id": 1, "Booking_Policy_Id": "staff"},
            ]

        def policy_by_id(self, policy_id, building):
            return student if policy_id == "student" else staff

    building = Building(36, "Building LIC", ZoneInfo("Europe/Brussels"), ())
    log = tmp_path / "observe.jsonl"
    now = datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)
    wanted = observe.targets(date(2026, 11, 16), [1, 2], [("09:00", "10:30")])
    observe.run(FakeRoamer(api=None), building, wanted, "MeetingRoom", log, now)
    lines = [json.loads(line) for line in log.read_text().splitlines()]
    assert lines[0]["ok"] and lines[0]["offset_days"] == 1 and lines[0]["free_total"] == 2
    assert [e["space_id"] for e in lines[0]["eligible"]] == ["LIC.2.06"]
    assert not lines[1]["ok"] and lines[1]["error_kind"] == "auth"
