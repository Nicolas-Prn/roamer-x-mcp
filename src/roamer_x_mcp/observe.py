"""Read-only availability observer: learn when bookable rooms are released and how fast they go.

Each run searches a few (date, slot) targets relative to today and appends one JSON line per target to a
log. Comparing successive lines shows when eligible rooms first appear for a date (release) and when they
disappear (taken). It never books anything.

A room is *eligible* when it can be booked without staff approval: its booking policy does not require
approval and neither the space nor its policy is marked STAFF ONLY.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config
from .booking import Building, Policy, Roamer
from .client import IbssError

DEFAULT_LOG = config.HOME / "observe.jsonl"


@dataclass(frozen=True)
class Target:
    day: date
    start: str
    end: str


def parse_offsets(text: str) -> list[int]:
    """`"0-8"`, `"6,7,8"` or a mix (`"0,5-8"`) → sorted day offsets."""
    out: set[int] = set()
    for part in text.split(","):
        lo, _, hi = part.strip().partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return sorted(out)


def parse_slot(text: str) -> tuple[str, str]:
    start, end = (s.strip() for s in text.split("-", 1))
    datetime.strptime(start, "%H:%M"), datetime.strptime(end, "%H:%M")
    return start, end


def targets(today: date, offsets: list[int], slots: list[tuple[str, str]]) -> list[Target]:
    return [Target(today + timedelta(days=o), s, e) for o in offsets for s, e in slots]


def is_eligible(space: dict, policy: Policy) -> bool:
    text = " ".join([space.get("Space_Name") or "", policy.name, policy.description]).upper()
    return not policy.approval_required and "STAFF" not in text


def error_kind(error: Exception) -> str:
    if isinstance(error, config.NotConfiguredError):
        return "login_required"
    status = getattr(error, "status", None)
    if status == 401:
        return "auth"
    if status is None and isinstance(error, IbssError):
        return "unreachable"
    return "api_error" if status else "error"


def observe_target(r: Roamer, building: Building, target: Target, space_type: str, now: datetime) -> dict:
    record = {
        "observed_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "date": target.day.isoformat(),
        "start": target.start,
        "end": target.end,
        "offset_days": (target.day - now.astimezone(building.tz).date()).days,
        "space_type": space_type,
    }
    try:
        s, e = r.window(building, target.day.isoformat(), target.start, target.end)
        spaces = r.search(building, s, e, space_type, None, None, 300)
        eligible = []
        for sp in spaces:
            pid = sp.get("Booking_Policy_Id")
            if not pid:
                continue
            policy = r.policy_by_id(pid, building)
            if is_eligible(sp, policy):
                eligible.append({
                    "space_id": sp["Space_Id"],
                    "capacity": sp.get("Space_Capacity"),
                    "policy": policy.name,
                    "horizon_days": policy.horizon_days,
                    "max_minutes": policy.max_minutes,
                })
        record.update(ok=True, free_total=len(spaces), eligible=eligible)
    except (IbssError, config.NotConfiguredError, ValueError) as err:
        record.update(ok=False, error_kind=error_kind(err), error=str(err)[:300])
    return record


def run(r: Roamer, building: Building, wanted: list[Target], space_type: str, log: Path,
        now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    records = [observe_target(r, building, t, space_type, now) for t in wanted]
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return records
