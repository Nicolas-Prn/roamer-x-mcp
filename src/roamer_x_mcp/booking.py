"""Room search and booking on top of the IBSS API, in the building's local time.

IBSS conventions this module hides from the tools:
- Locations are a tree of nodes (root → region → building → floor). Space and booking routes must be called
  on the space's **floor** node; searches work on the building node.
- `spaces/search` only accepts filters that include `AvailableBetween(start, end)` (UTC).
- Booking times are sent in UTC: local times with an offset (what the Hub sends) get shifted by the API.
- Booking rules (duration, horizon, opening hours, approval) live in per-space booking policies.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .client import Ibss, IbssError

ROOT_NODE = 1
DEFAULT_BUILDING = 36  # Building LIC (ULB/VUB)

SEARCH_FIELDS = [
    "Space_Id", "Space_Name", "Space_Type", "Space_Capacity", "Node_Id", "Booking_Policy_Id", "Meta_Bookable",
]
BOOKING_FIELDS = [
    "Node_Id", "Space_Id", "Space_Name", "Booking_Id", "Booking_Name", "Booking_Start", "Booking_End",
    "Booking_Status", "Booking_Early_Checkin", "Booking_Participant_CheckedIn",
]


@dataclass(frozen=True)
class Floor:
    id: int
    name: str


@dataclass(frozen=True)
class Building:
    id: int
    name: str
    tz: ZoneInfo
    floors: tuple[Floor, ...]


@dataclass(frozen=True)
class Policy:
    name: str
    description: str
    min_minutes: int | None
    max_minutes: int | None
    horizon_days: int | None
    approval_required: bool
    opening_hours: list[str]
    auto_checkin: bool

    def summary(self) -> str:
        parts = [f"{self.name}: {self.description}".strip(": ")]
        if self.min_minutes or self.max_minutes:
            parts.append(f"duration {self.min_minutes or 0}–{self.max_minutes or '∞'} min")
        if self.horizon_days is not None:
            parts.append(f"bookable up to {self.horizon_days} days ahead")
        if self.opening_hours:
            parts.append("hours " + ", ".join(self.opening_hours))
        if self.approval_required:
            parts.append("needs approval")
        if self.auto_checkin:
            parts.append("auto check-in")
        return "; ".join(parts)


def _parse_policy(raw: dict) -> Policy:
    pol = raw.get("Booking_Policy", raw)
    if isinstance(pol, str):
        pol = json.loads(pol)
    slots = pol.get("BookingSlots", {})
    duration = slots.get("BookingDuration", {})
    horizon = slots.get("BookingHorizon", {})
    days = {"1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun"}
    hours = []
    for b in slots.get("BookableTime", []):
        d = b.get("Days", [])
        span = "every day" if len(d) == 7 else "/".join(days.get(x, x) for x in d)
        hours.append(f"{span} {b.get('StartTime')}–{b.get('EndTime')}")
    return Policy(
        name=raw.get("Booking_Policy_Name", ""),
        description=(pol.get("Booking_Policy_Description") or "").strip(),
        min_minutes=duration.get("Minimum"),
        max_minutes=duration.get("Maximum"),
        horizon_days=horizon.get("Maximum"),
        approval_required=bool(pol.get("ApprovalPolicies", {}).get("ApprovalRequired")),
        opening_hours=hours,
        auto_checkin=bool(pol.get("ArrivalPolicies", {}).get("AutoCheckin")),
    )


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_api_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _odata_str(value: str) -> str:
    return value.replace("'", "''")


class Roamer:
    def __init__(self, api: Ibss):
        self.api = api
        self._buildings: dict[int, Building] | None = None
        self._space_floor: dict[str, int] = {}
        self._policies: dict[str, Policy] = {}

    # ------------------------------------------------------------------ locations

    def buildings(self) -> dict[int, Building]:
        if self._buildings is None:
            root = self.api.get(f"v1/{ROOT_NODE}/Nodes")
            found = {}
            for region in root.get("Regions", []):
                for b in region.get("Buildings", []):
                    floors = tuple(sorted(
                        (Floor(f["Node_Id"], f["Node_Name"]) for f in b.get("Floors", []) if f.get("Node_IsEnabled", 1)),
                        key=lambda f: f.id,
                    ))
                    found[b["Node_Id"]] = Building(b["Node_Id"], b["Name"], ZoneInfo(b.get("Building_TZ") or "UTC"), floors)
            self._buildings = found
        return self._buildings

    def building(self, building_id: int | None = None) -> Building:
        bid = building_id or DEFAULT_BUILDING
        try:
            return self.buildings()[bid]
        except KeyError:
            raise ValueError(f"unknown building id {bid}; call list_locations to see the valid ones") from None

    def floor_name(self, node_id: int) -> str:
        for b in self.buildings().values():
            for f in b.floors:
                if f.id == node_id:
                    return f.name
        return f"node {node_id}"

    def resolve_floor(self, floor: str | int | None, building: Building) -> int | None:
        """Accept a floor node id, a floor number ("-1", "4") or a floor name ("Floor 04")."""
        if floor is None or str(floor).strip() == "":
            return None
        text = str(floor).strip()
        for f in building.floors:
            if text == str(f.id) or text.lower() == f.name.lower():
                return f.id
        try:
            level = int(text)
        except ValueError:
            level = None
        for f in building.floors:
            digits = f.name.split()[-1]
            if level is not None and digits.lstrip("-").isdigit() and int(digits) == level:
                return f.id
        raise ValueError(f"unknown floor {floor!r}; floors of {building.name}: "
                         + ", ".join(f"{f.name} (id {f.id})" for f in building.floors))

    # ------------------------------------------------------------------ time

    def window(self, building: Building, day: str, start: str, end: str) -> tuple[datetime, datetime]:
        try:
            d = date.fromisoformat(day)
            s = datetime.combine(d, time.fromisoformat(start), building.tz)
            e = datetime.combine(d, time.fromisoformat(end), building.tz)
        except ValueError as err:
            raise ValueError(f"invalid date/time ({err}); use date=YYYY-MM-DD, start/end=HH:MM") from None
        if e <= s:
            raise ValueError("end must be after start")
        return s, e

    # ------------------------------------------------------------------ spaces

    def space_types(self, building: Building) -> dict[str, list[str]]:
        groups = self.api.get(f"v2/{building.id}/nodes/space-types")
        out: dict[str, list[str]] = {}
        for group in groups:
            for name, items in group.items():
                if items:
                    out[name] = [i["Name"] for i in items]
        return out

    def search(self, building: Building, start: datetime, end: datetime, space_type: str | None,
               floor_id: int | None, min_capacity: int | None, limit: int) -> list[dict]:
        clauses = ["(Space_IsEnabled eq 1)"]
        if space_type:
            clauses.append(f"(Space_Type eq '{_odata_str(space_type)}')")
        if floor_id is not None:
            clauses.append(f"(Node_Id eq {floor_id})")
        clauses.append(f"AvailableBetween(datetime'{_utc_iso(start)}', datetime'{_utc_iso(end)}')")
        if min_capacity:
            clauses.append(f"Space_Capacity ge {int(min_capacity)}")
        spaces = self.api.get_all(
            f"v2/{building.id}/spaces/search", limit=limit,
            **{"$filter": " and ".join(clauses), "$top": min(limit, 100), "$select": ",".join(SEARCH_FIELDS)},
        )
        for s in spaces:
            self._space_floor[s["Space_Id"]] = s["Node_Id"]
        return spaces

    def space_floor(self, space_id: str, building: Building) -> int:
        if space_id in self._space_floor:
            return self._space_floor[space_id]
        for f in building.floors:
            try:
                self.api.get(f"v2/{f.id}/spaces/{space_id}")
            except IbssError as e:
                if e.status == 404:
                    continue
                raise
            self._space_floor[space_id] = f.id
            return f.id
        raise ValueError(f"space {space_id!r} not found in {building.name}; use search_spaces to find space ids")

    def space(self, space_id: str, building: Building) -> dict:
        return self.api.get(f"v2/{self.space_floor(space_id, building)}/spaces/{space_id}")

    def policy(self, space_id: str, building: Building) -> Policy:
        if space_id not in self._policies:
            raw = self.api.get(f"v2/{self.space_floor(space_id, building)}/spaces/{space_id}/booking-policy")
            self._policies[space_id] = _parse_policy(raw)
        return self._policies[space_id]

    def policy_by_id(self, policy_id: str, building: Building) -> Policy:
        key = "policy:" + policy_id
        if key not in self._policies:
            self._policies[key] = _parse_policy(self.api.get(f"v2/{building.id}/bookingpolicies/{policy_id}"))
        return self._policies[key]

    # ------------------------------------------------------------------ bookings

    def my_bookings(self, start: datetime, end: datetime) -> list[dict]:
        return self.api.get_all(
            f"v2/{ROOT_NODE}/bookings/my-bookings", limit=200,
            **{
                "$select": ",".join(BOOKING_FIELDS),
                "$filter": f"Booking_Start ge datetime'{_utc_iso(start)}' and Booking_End lt datetime'{_utc_iso(end)}'",
                "$top": 100,
                "$orderby": "Booking_Start",
            },
        )

    def find_booking(self, booking_id: str) -> dict:
        now = datetime.now(timezone.utc)
        for b in self.my_bookings(now - timedelta(days=1), now + timedelta(days=90)):
            if b["Booking_Id"] == booking_id:
                return b
        raise ValueError(f"booking {booking_id!r} not found among your bookings (yesterday to +90 days)")

    def check_policy(self, policy: Policy, start: datetime, end: datetime) -> None:
        """Mirror the policy limits locally so the model gets a clear reason instead of an opaque API error."""
        minutes = (end - start).total_seconds() / 60
        if policy.max_minutes and minutes > policy.max_minutes:
            raise ValueError(f"{minutes:.0f} min exceeds the maximum of {policy.max_minutes} min ({policy.name})")
        if policy.min_minutes and minutes < policy.min_minutes:
            raise ValueError(f"{minutes:.0f} min is below the minimum of {policy.min_minutes} min ({policy.name})")
        if start < datetime.now(start.tzinfo) - timedelta(minutes=5):
            raise ValueError("cannot book in the past")
        if policy.horizon_days is not None:
            limit = datetime.now(start.tzinfo).date() + timedelta(days=policy.horizon_days)
            if start.date() > limit:
                raise ValueError(f"too far ahead: {policy.name} allows booking up to {policy.horizon_days} days "
                                 f"in advance (until {limit.isoformat()})")

    def book(self, space_id: str, building: Building, start: datetime, end: datetime, name: str,
             description: str = "") -> dict:
        floor_id = self.space_floor(space_id, building)
        self.check_policy(self.policy(space_id, building), start, end)
        body = {
            "_CreatedAt": "",
            "_CreatedBy": self.user_email(),
            "SpaceId": space_id,
            "Booking_Start": _utc_iso(start),
            "Booking_End": _utc_iso(end),
            "Booking_Name": name or self.user_name() or "Booking",
            "Booking_Description": description,
            "Booking_AutoCheckIn": 1,
            "DisableExtUpdate": False,
            "Cost_Code_Allocation": [],
            "UseOnBehalfOfCostCodes": False,
            "AddOnlineMeetingLink": 0,
            "Booking_Attendance": 1,
        }
        res = self.api.post(f"v2/{floor_id}/bookings", body) or {}
        if not res.get("Booking_Id"):
            # The create response does not always carry the id: find the new booking among the user's ones.
            for b in self.my_bookings(start - timedelta(minutes=1), end + timedelta(minutes=1)):
                if b["Space_Id"] == space_id and _parse_api_time(b["Booking_Start"]) == start:
                    res = {**res, **b}
                    break
        return res

    def cancel(self, booking_id: str) -> dict:
        booking = self.find_booking(booking_id)
        self.api.patch(f"v2/{booking['Node_Id']}/bookings/{booking_id}/cancel")
        return booking

    def _claims(self) -> dict:
        try:
            payload = self.api.token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return json.loads(base64.urlsafe_b64decode(payload))
        except (IndexError, ValueError):
            return {}

    def user_email(self) -> str:
        claims = self._claims()
        return claims.get("email") or claims.get("preferred_username") or ""

    def user_name(self) -> str:
        return self._claims().get("name") or ""

    def local(self, value: str, building: Building) -> datetime:
        return _parse_api_time(value).astimezone(building.tz)
