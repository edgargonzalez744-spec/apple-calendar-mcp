"""MCP server for Apple Calendar (iCloud) over CalDAV.

Exposes the same tool surface as the Google Calendar connector:
    list_calendars, list_events, get_event, create_event,
    update_event, delete_event, respond_to_event, suggest_time
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .caldav_client import ConfigError, get_client

# DNS-rebinding protection defaults to allowing only localhost, which 421s the
# request ("Invalid Host header") when deployed behind a proxy (Hugging Face,
# Render, etc.). It guards browser-based attacks; this connector is reached
# server-to-server by Claude and is gated by the secret URL path, so we turn it
# off. Override with MCP_ALLOWED_HOSTS (comma-separated) to re-enable + scope it.
_allowed_hosts = [h for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h]
_security = (
    TransportSecuritySettings(allowed_hosts=_allowed_hosts, allowed_origins=["*"])
    if _allowed_hosts
    else TransportSecuritySettings(enable_dns_rebinding_protection=False)
)

mcp = FastMCP("apple-calendar", transport_security=_security)


def _client():
    try:
        return get_client()
    except ConfigError as exc:
        raise RuntimeError(str(exc)) from exc


@mcp.tool()
def list_calendars() -> list[dict[str, Any]]:
    """List the user's Apple/iCloud calendars.

    Returns each calendar's `id` (used as `calendar_id` in other tools),
    display `name`, and `color`.
    """
    return _client().list_calendars()


@mcp.tool()
def list_events(
    time_min: str | None = None,
    time_max: str | None = None,
    calendar_id: str | None = None,
    query: str | None = None,
    max_results: int = 100,
) -> list[dict[str, Any]]:
    """List events between `time_min` and `time_max` (ISO-8601).

    Args:
        time_min: Start of range, ISO-8601 (default: now).
        time_max: End of range, ISO-8601 (default: 30 days after time_min).
        calendar_id: Restrict to one calendar; omit to search all calendars.
        query: Case-insensitive substring filter on summary/description.
        max_results: Cap on returned events.
    """
    return _client().list_events(
        calendar_id=calendar_id,
        time_min=time_min,
        time_max=time_max,
        query=query,
        max_results=max_results,
    )


@mcp.tool()
def get_event(calendar_id: str, event_id: str) -> dict[str, Any]:
    """Fetch a single event by its `event_id` (UID) from `calendar_id`."""
    return _client().get_event(calendar_id=calendar_id, event_id=event_id)


@mcp.tool()
def create_event(
    calendar_id: str,
    summary: str,
    start: str,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    all_day: bool = False,
    attendees: list[str] | None = None,
    recurrence: str | None = None,
    reminders_minutes: list[int] | None = None,
) -> dict[str, Any]:
    """Create an event.

    Args:
        calendar_id: Target calendar (`id` from list_calendars).
        summary: Event title.
        start: Start time, ISO-8601. For all-day use a date like "2026-07-01".
        end: End time, ISO-8601. Defaults to +1h (timed) or +1 day (all-day).
        description: Notes/body.
        location: Free-text location.
        all_day: True for an all-day event.
        attendees: List of email addresses to invite.
        recurrence: Raw RRULE, e.g. "FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10".
        reminders_minutes: Alert offsets in minutes before start, e.g. [10, 60].
    """
    return _client().create_event(
        calendar_id=calendar_id,
        summary=summary,
        start=start,
        end=end,
        description=description,
        location=location,
        all_day=all_day,
        attendees=attendees,
        recurrence=recurrence,
        reminders_minutes=reminders_minutes,
    )


@mcp.tool()
def update_event(
    calendar_id: str,
    event_id: str,
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    all_day: bool | None = None,
    attendees: list[str] | None = None,
    recurrence: str | None = None,
) -> dict[str, Any]:
    """Update fields on an existing event. Only provided fields change."""
    fields: dict[str, Any] = {}
    for key, value in (
        ("summary", summary),
        ("start", start),
        ("end", end),
        ("description", description),
        ("location", location),
        ("attendees", attendees),
        ("recurrence", recurrence),
    ):
        if value is not None:
            fields[key] = value
    if all_day is not None:
        fields["all_day"] = all_day
    return _client().update_event(calendar_id=calendar_id, event_id=event_id, **fields)


@mcp.tool()
def delete_event(calendar_id: str, event_id: str) -> dict[str, Any]:
    """Delete an event by `event_id` (UID) from `calendar_id`."""
    return _client().delete_event(calendar_id=calendar_id, event_id=event_id)


@mcp.tool()
def respond_to_event(calendar_id: str, event_id: str, response: str) -> dict[str, Any]:
    """RSVP to an event you were invited to.

    Args:
        response: One of "accepted", "declined", or "tentative".
    """
    return _client().respond_to_event(
        calendar_id=calendar_id, event_id=event_id, response=response
    )


@mcp.tool()
def suggest_time(
    duration_minutes: int,
    time_min: str,
    time_max: str,
    calendar_ids: list[str] | None = None,
    working_hours_start: int = 9,
    working_hours_end: int = 17,
    max_suggestions: int = 5,
) -> list[dict[str, Any]]:
    """Suggest free time slots that avoid conflicts.

    Scans busy events across the given calendars (or all calendars) within
    [time_min, time_max] and returns open slots of `duration_minutes`, on
    weekdays within working hours.

    Args:
        duration_minutes: Desired meeting length.
        time_min: Earliest ISO-8601 time to consider.
        time_max: Latest ISO-8601 time to consider.
        calendar_ids: Calendars to check for conflicts (default: all).
        working_hours_start: Earliest hour (0-23) to suggest.
        working_hours_end: Latest hour (0-23) a slot may end.
        max_suggestions: Maximum number of slots to return.
    """
    return _client().suggest_time(
        duration_minutes=duration_minutes,
        time_min=time_min,
        time_max=time_max,
        calendar_ids=calendar_ids,
        working_hours=(working_hours_start, working_hours_end),
        max_suggestions=max_suggestions,
    )


def main() -> None:
    """Entry point for the `apple-calendar-mcp` console script (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
