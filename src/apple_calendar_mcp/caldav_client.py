"""Thin wrapper around the `caldav` library for Apple iCloud Calendar.

iCloud exposes calendars over CalDAV at https://caldav.icloud.com/. Auth is the
Apple ID email plus an *app-specific password* (Apple does not allow your normal
password for third-party clients). Generate one at https://account.apple.com
under "Sign-In and Security" → "App-Specific Passwords".

This module hides CalDAV/iCalendar details and speaks plain JSON-serialisable
dicts, so the MCP layer in `server.py` stays small.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import urllib.request
import uuid
from functools import lru_cache
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import caldav
import recurring_ical_events
from caldav.lib.error import NotFoundError
from dateutil import parser as date_parser
from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent
from icalendar import vCalAddress, vText

# Subscription (ICS/webcal) feeds that iCloud does NOT expose over CalDAV. Users
# paste the feed URLs into EXTRA_ICS_FEEDS (whitespace- or comma-separated) and
# we read them directly from the source. These are read-only.
FEED_ID_PREFIX = "feed:"

ICLOUD_CALDAV_URL = "https://caldav.icloud.com/"

PARTSTAT_MAP = {
    "accepted": "ACCEPTED",
    "accept": "ACCEPTED",
    "declined": "DECLINED",
    "decline": "DECLINED",
    "tentative": "TENTATIVE",
    "maybe": "TENTATIVE",
    "needs-action": "NEEDS-ACTION",
    "needsaction": "NEEDS-ACTION",
}


class ConfigError(RuntimeError):
    """Raised when required credentials are missing."""


def _default_tz() -> ZoneInfo:
    name = os.environ.get("CALDAV_DEFAULT_TZ", "UTC")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _parse_dt(value: str, *, all_day: bool = False) -> dt.date | dt.datetime:
    """Parse an ISO-8601 string into a date (all-day) or tz-aware datetime."""
    parsed = date_parser.isoparse(value) if "T" in value or " " in value else date_parser.parse(value)
    if all_day:
        return parsed.date() if isinstance(parsed, dt.datetime) else parsed
    if isinstance(parsed, dt.date) and not isinstance(parsed, dt.datetime):
        parsed = dt.datetime(parsed.year, parsed.month, parsed.day)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_default_tz())
    return parsed


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


class AppleCalendarClient:
    """Connects to iCloud CalDAV and performs calendar operations."""

    def __init__(self, username: str | None = None, password: str | None = None,
                 url: str | None = None) -> None:
        self.username = username or os.environ.get("ICLOUD_USERNAME")
        self.password = password or os.environ.get("ICLOUD_APP_PASSWORD")
        self.url = url or os.environ.get("CALDAV_URL", ICLOUD_CALDAV_URL)
        if not self.username or not self.password:
            raise ConfigError(
                "Missing credentials. Set ICLOUD_USERNAME (your Apple ID email) and "
                "ICLOUD_APP_PASSWORD (an app-specific password from account.apple.com)."
            )
        self._client: caldav.DAVClient | None = None
        self._principal: caldav.Principal | None = None

    # -- connection -------------------------------------------------------
    @property
    def principal(self) -> caldav.Principal:
        if self._principal is None:
            self._client = caldav.DAVClient(
                url=self.url, username=self.username, password=self.password
            )
            self._principal = self._client.principal()
        return self._principal

    def _calendars(self) -> list[caldav.Calendar]:
        return self.principal.calendars()

    def _find_calendar(self, calendar_id: str) -> caldav.Calendar:
        # calendar_id is the calendar URL (what list_calendars returns as `id`).
        if calendar_id.startswith(FEED_ID_PREFIX):
            raise ValueError(
                "This is a read-only subscription calendar; events can be listed "
                "but not created, changed, or deleted."
            )
        target = calendar_id.rstrip("/")
        for cal in self._calendars():
            if str(cal.url).rstrip("/") == target:
                return cal
        # Fall back to matching by display name for convenience.
        for cal in self._calendars():
            try:
                if cal.get_display_name() == calendar_id:
                    return cal
            except Exception:
                continue
        raise NotFoundError(f"No calendar matching id/name: {calendar_id!r}")

    # -- calendars --------------------------------------------------------
    def list_calendars(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for cal in self._calendars():
            try:
                name = cal.get_display_name()
            except Exception:
                name = str(cal.url).rstrip("/").rsplit("/", 1)[-1]
            props: dict[str, Any] = {}
            try:
                comps = cal.get_supported_components()
            except Exception:
                comps = []
            # Skip non-event collections (reminders/tasks) unless they also hold events.
            if comps and "VEVENT" not in comps:
                continue
            color = None
            try:
                color = cal.get_property(caldav.elements.ical.CalendarColor())
            except Exception:
                pass
            out.append({
                "id": str(cal.url),
                "name": name,
                "color": color,
                "supported_components": comps,
                **props,
            })
        # Append subscription feeds (holidays, sports, etc.) that iCloud's CalDAV
        # interface does not expose.
        out.extend(list_feed_calendars())
        return out

    # -- events -----------------------------------------------------------
    def list_events(self, calendar_id: str | None, time_min: str | None,
                    time_max: str | None, query: str | None = None,
                    max_results: int = 100) -> list[dict[str, Any]]:
        start = _parse_dt(time_min) if time_min else dt.datetime.now(_default_tz())
        end = _parse_dt(time_max) if time_max else start + dt.timedelta(days=30)

        events: list[dict[str, Any]] = []

        # Subscription feeds (read-only, not in iCloud CalDAV).
        if calendar_id and calendar_id.startswith(FEED_ID_PREFIX):
            events.extend(feed_events(calendar_id[len(FEED_ID_PREFIX):], start, end, query))
            events.sort(key=lambda e: e.get("start") or "")
            return events[:max_results]
        if not calendar_id:
            for url in parse_feed_env():
                events.extend(feed_events(url, start, end, query))

        calendars = [self._find_calendar(calendar_id)] if calendar_id else self._calendars()
        for cal in calendars:
            try:
                comps = cal.get_supported_components()
                if comps and "VEVENT" not in comps:
                    continue
            except Exception:
                pass
            try:
                found = cal.search(
                    start=start, end=end, event=True, expand=True,
                )
            except Exception:
                found = cal.date_search(start=start, end=end, expand=True)
            for ev in found:
                for d in self._event_to_dicts(ev, str(cal.url)):
                    if query and query.lower() not in (
                        (d.get("summary") or "") + " " + (d.get("description") or "")
                    ).lower():
                        continue
                    events.append(d)

        events.sort(key=lambda e: e.get("start") or "")
        return events[:max_results]

    def get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        cal = self._find_calendar(calendar_id)
        ev = cal.event_by_uid(event_id)
        dicts = self._event_to_dicts(ev, str(cal.url))
        return dicts[0] if dicts else {}

    def create_event(self, calendar_id: str, summary: str, start: str, end: str | None,
                     description: str | None = None, location: str | None = None,
                     all_day: bool = False, attendees: Iterable[str] | None = None,
                     recurrence: str | None = None,
                     reminders_minutes: Iterable[int] | None = None) -> dict[str, Any]:
        cal = self._find_calendar(calendar_id)
        uid = str(uuid.uuid4())

        cal_obj = ICalendar()
        cal_obj.add("prodid", "-//apple-calendar-mcp//EN")
        cal_obj.add("version", "2.0")
        vevent = IEvent()
        vevent.add("uid", uid)
        vevent.add("dtstamp", dt.datetime.now(dt.timezone.utc))
        vevent.add("summary", summary)

        self._apply_times(vevent, start, end, all_day)
        if description:
            vevent.add("description", description)
        if location:
            vevent.add("location", location)
        if recurrence:
            # recurrence is a raw RRULE string, e.g. "FREQ=WEEKLY;BYDAY=MO,WE"
            vevent.add("rrule", _parse_rrule(recurrence))
        for email in attendees or []:
            vevent.add("attendee", _attendee(email))
        for minutes in reminders_minutes or []:
            vevent.add_component(_alarm(minutes))

        cal_obj.add_component(vevent)
        saved = cal.save_event(cal_obj.to_ical().decode("utf-8"))
        dicts = self._event_to_dicts(saved, str(cal.url))
        return dicts[0] if dicts else {"uid": uid}

    def update_event(self, calendar_id: str, event_id: str, **fields: Any) -> dict[str, Any]:
        cal = self._find_calendar(calendar_id)
        ev = cal.event_by_uid(event_id)
        ical = ev.icalendar_instance
        vevent = next(c for c in ical.walk("VEVENT"))

        def _set(name: str, value: Any) -> None:
            if name in vevent:
                del vevent[name]
            if value is not None:
                vevent.add(name, value)

        if fields.get("summary") is not None:
            _set("summary", fields["summary"])
        if fields.get("description") is not None:
            _set("description", fields["description"])
        if fields.get("location") is not None:
            _set("location", fields["location"])

        if fields.get("start") is not None or fields.get("end") is not None or "all_day" in fields:
            all_day = bool(fields.get("all_day", _is_all_day(vevent)))
            start = fields.get("start") or _iso(vevent.get("dtstart").dt)
            end = fields.get("end")
            if end is None and vevent.get("dtend") is not None:
                end = _iso(vevent.get("dtend").dt)
            for key in ("dtstart", "dtend"):
                if key in vevent:
                    del vevent[key]
            self._apply_times(vevent, start, end, all_day)

        if fields.get("recurrence") is not None:
            _set("rrule", _parse_rrule(fields["recurrence"]))
        if fields.get("attendees") is not None:
            if "attendee" in vevent:
                del vevent["attendee"]
            for email in fields["attendees"]:
                vevent.add("attendee", _attendee(email))

        # bump last-modified so clients re-sync
        _set("last-modified", dt.datetime.now(dt.timezone.utc))

        ev.data = ical.to_ical().decode("utf-8")
        ev.save()
        dicts = self._event_to_dicts(ev, str(cal.url))
        return dicts[0] if dicts else {"uid": event_id}

    def delete_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        cal = self._find_calendar(calendar_id)
        ev = cal.event_by_uid(event_id)
        ev.delete()
        return {"deleted": True, "event_id": event_id, "calendar_id": calendar_id}

    def respond_to_event(self, calendar_id: str, event_id: str, response: str) -> dict[str, Any]:
        partstat = PARTSTAT_MAP.get(response.strip().lower())
        if partstat is None:
            raise ValueError(
                f"Invalid response {response!r}. Use accepted/declined/tentative."
            )
        cal = self._find_calendar(calendar_id)
        ev = cal.event_by_uid(event_id)
        ical = ev.icalendar_instance
        vevent = next(c for c in ical.walk("VEVENT"))

        me = f"mailto:{self.username.lower()}"
        updated = False
        attendees = vevent.get("attendee")
        if attendees is not None:
            if not isinstance(attendees, list):
                attendees = [attendees]
            for att in attendees:
                if str(att).lower() == me:
                    att.params["PARTSTAT"] = partstat
                    updated = True
        if not updated:
            att = _attendee(self.username)
            att.params["PARTSTAT"] = partstat
            vevent.add("attendee", att)

        ev.data = ical.to_ical().decode("utf-8")
        ev.save()
        return {"event_id": event_id, "response": partstat, "updated": updated}

    def suggest_time(self, duration_minutes: int, time_min: str, time_max: str,
                     calendar_ids: Iterable[str] | None = None,
                     working_hours: tuple[int, int] = (9, 17),
                     max_suggestions: int = 5,
                     slot_granularity_minutes: int = 30) -> list[dict[str, Any]]:
        """Find free slots of `duration_minutes` by scanning busy events."""
        window_start = _parse_dt(time_min)
        window_end = _parse_dt(time_max)
        tz = window_start.tzinfo or _default_tz()

        cals = (
            [self._find_calendar(c) for c in calendar_ids]
            if calendar_ids else self._calendars()
        )
        busy: list[tuple[dt.datetime, dt.datetime]] = []
        for cal in cals:
            try:
                comps = cal.get_supported_components()
                if comps and "VEVENT" not in comps:
                    continue
            except Exception:
                pass
            try:
                found = cal.search(start=window_start, end=window_end, event=True, expand=True)
            except Exception:
                continue
            for ev in found:
                for d in self._event_to_dicts(ev, str(cal.url)):
                    if d.get("transparent") or d.get("all_day"):
                        continue
                    s, e = d.get("start"), d.get("end")
                    if not s or not e:
                        continue
                    busy.append((_as_dt(s, tz), _as_dt(e, tz)))
        busy.sort()

        duration = dt.timedelta(minutes=duration_minutes)
        step = dt.timedelta(minutes=slot_granularity_minutes)
        suggestions: list[dict[str, Any]] = []
        cursor = window_start
        while cursor + duration <= window_end and len(suggestions) < max_suggestions:
            slot_end = cursor + duration
            local = cursor.astimezone(tz)
            in_hours = working_hours[0] <= local.hour and slot_end.astimezone(tz).hour <= working_hours[1]
            is_weekday = local.weekday() < 5
            conflict = any(s < slot_end and cursor < e for s, e in busy)
            if in_hours and is_weekday and not conflict:
                suggestions.append({"start": cursor.isoformat(), "end": slot_end.isoformat()})
                cursor = slot_end
            else:
                cursor += step
        return suggestions

    # -- helpers ----------------------------------------------------------
    def _apply_times(self, vevent: IEvent, start: str, end: str | None, all_day: bool) -> None:
        dtstart = _parse_dt(start, all_day=all_day)
        vevent.add("dtstart", dtstart)
        if all_day:
            if end:
                dtend = _parse_dt(end, all_day=True)
            else:
                dtend = (dtstart + dt.timedelta(days=1))
            vevent.add("dtend", dtend)
        else:
            if end:
                vevent.add("dtend", _parse_dt(end))
            else:
                vevent.add("dtend", dtstart + dt.timedelta(hours=1))

    def _event_to_dicts(self, caldav_event: caldav.Event, calendar_id: str) -> list[dict[str, Any]]:
        try:
            ical = caldav_event.icalendar_instance
        except Exception:
            ical = ICalendar.from_ical(caldav_event.data)
        return [_component_to_dict(comp, calendar_id) for comp in ical.walk("VEVENT")]


# -- subscription (ICS/webcal) feeds -------------------------------------
def _normalize_feed_url(url: str) -> str:
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    return url


def parse_feed_env() -> list[str]:
    """Read EXTRA_ICS_FEEDS — whitespace/comma-separated feed URLs."""
    raw = os.environ.get("EXTRA_ICS_FEEDS", "")
    urls = [u for u in re.split(r"[\s,]+", raw) if u.strip()]
    return [_normalize_feed_url(u) for u in urls]


@lru_cache(maxsize=64)
def _fetch_feed(url: str, _bucket: int) -> bytes:
    """Fetch raw ICS bytes. `_bucket` is a coarse time bucket so the cache
    refreshes periodically without a real clock in the hot path."""
    req = urllib.request.Request(url, headers={"User-Agent": "apple-calendar-mcp"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (user-provided URL)
        return resp.read()


def _feed_calendar(url: str) -> ICalendar | None:
    try:
        bucket = int(dt.datetime.now(dt.timezone.utc).timestamp() // 900)  # 15-min cache
        return ICalendar.from_ical(_fetch_feed(url, bucket))
    except Exception:
        return None


def _feed_name(cal: ICalendar, url: str) -> str:
    name = cal.get("X-WR-CALNAME")
    if name:
        return str(name)
    return url.rstrip("/").rsplit("/", 1)[-1] or url


def list_feed_calendars() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for url in parse_feed_env():
        cal = _feed_calendar(url)
        name = _feed_name(cal, url) if cal else url
        out.append({
            "id": f"{FEED_ID_PREFIX}{url}",
            "name": name,
            "color": None,
            "supported_components": ["VEVENT"],
            "read_only": True,
            "subscription": True,
        })
    return out


def feed_events(url: str, start: dt.datetime, end: dt.datetime,
                query: str | None = None) -> list[dict[str, Any]]:
    cal = _feed_calendar(url)
    if cal is None:
        return []
    calendar_id = f"{FEED_ID_PREFIX}{url}"
    out: list[dict[str, Any]] = []
    try:
        occurrences = recurring_ical_events.of(cal).between(start, end)
    except Exception:
        occurrences = [c for c in cal.walk("VEVENT")]
    for comp in occurrences:
        d = _component_to_dict(comp, calendar_id)
        d["read_only"] = True
        if query and query.lower() not in (
            (d.get("summary") or "") + " " + (d.get("description") or "")
        ).lower():
            continue
        out.append(d)
    return out


# -- module-level iCal helpers -------------------------------------------
def _component_to_dict(comp: Any, calendar_id: str) -> dict[str, Any]:
    dtstart = comp.get("dtstart")
    dtend = comp.get("dtend")
    attendees = comp.get("attendee")
    if attendees is not None and not isinstance(attendees, list):
        attendees = [attendees]
    return {
        "id": str(comp.get("uid")),
        "uid": str(comp.get("uid")),
        "calendar_id": calendar_id,
        "summary": str(comp.get("summary")) if comp.get("summary") else None,
        "description": str(comp.get("description")) if comp.get("description") else None,
        "location": str(comp.get("location")) if comp.get("location") else None,
        "start": _iso(dtstart.dt) if dtstart else None,
        "end": _iso(dtend.dt) if dtend else None,
        "all_day": _is_all_day(comp),
        "status": str(comp.get("status")) if comp.get("status") else None,
        "transparent": str(comp.get("transp", "OPAQUE")).upper() == "TRANSPARENT",
        "organizer": str(comp.get("organizer")) if comp.get("organizer") else None,
        "attendees": [
            {
                "email": str(a).replace("mailto:", ""),
                "status": a.params.get("PARTSTAT"),
                "name": a.params.get("CN"),
            }
            for a in (attendees or [])
        ],
        "recurrence": str(comp.get("rrule").to_ical().decode()) if comp.get("rrule") else None,
        "html_link": None,
    }


def _is_all_day(comp: Any) -> bool:
    dtstart = comp.get("dtstart")
    if dtstart is None:
        return False
    return isinstance(dtstart.dt, dt.date) and not isinstance(dtstart.dt, dt.datetime)


def _attendee(email: str) -> vCalAddress:
    addr = email if email.startswith("mailto:") else f"mailto:{email}"
    att = vCalAddress(addr)
    att.params["ROLE"] = vText("REQ-PARTICIPANT")
    att.params["PARTSTAT"] = vText("NEEDS-ACTION")
    att.params["RSVP"] = vText("TRUE")
    return att


def _alarm(minutes: int):
    from icalendar import Alarm

    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", "Reminder")
    alarm.add("trigger", dt.timedelta(minutes=-abs(minutes)))
    return alarm


def _parse_rrule(rrule: str) -> dict[str, Any]:
    """Turn 'FREQ=WEEKLY;BYDAY=MO,WE' into the dict icalendar expects."""
    out: dict[str, Any] = {}
    for part in rrule.replace("RRULE:", "").split(";"):
        if not part.strip():
            continue
        key, _, val = part.partition("=")
        out[key.strip().upper()] = [v.strip() for v in val.split(",")] if "," in val else val.strip()
    return out


def _as_dt(value: str, tz: ZoneInfo) -> dt.datetime:
    parsed = date_parser.isoparse(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


@lru_cache(maxsize=1)
def get_client() -> AppleCalendarClient:
    return AppleCalendarClient()
