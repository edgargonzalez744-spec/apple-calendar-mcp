# Apple Calendar MCP connector

A Claude **connector** (MCP server) for **Apple Calendar / iCloud**, built to mirror the
Google Calendar connector. It exposes the same tools so Claude can manage your iCloud
calendar the same way it manages Google Calendar:

| Tool | What it does |
|------|--------------|
| `list_calendars` | List your iCloud calendars |
| `list_events` | List events in a time range (optionally filtered/queried) |
| `get_event` | Fetch one event by id |
| `create_event` | Create an event (timed or all-day, attendees, recurrence, reminders) |
| `update_event` | Update fields on an existing event |
| `delete_event` | Delete an event |
| `respond_to_event` | RSVP accepted / declined / tentative |
| `suggest_time` | Find free slots that avoid conflicts |

Unlike Google, Apple has no public REST/OAuth calendar API, so this connector talks to
iCloud over **CalDAV** (`https://caldav.icloud.com/`).

## 1. Prerequisites

- macOS/Linux/Windows with [`uv`](https://docs.astral.sh/uv/) installed.
- An **app-specific password** for your Apple ID (Apple blocks your normal password for
  third-party clients):
  1. Go to <https://account.apple.com> → **Sign-In and Security** → **App-Specific Passwords**.
  2. Create one (e.g. name it "Claude calendar"). You'll get something like `abcd-efgh-ijkl-mnop`.

## 2. Install

```bash
cd ~/apple-calendar-mcp
uv sync          # creates the venv and installs deps
```

Quick check that it runs:

```bash
ICLOUD_USERNAME=you@icloud.com ICLOUD_APP_PASSWORD=abcd-efgh-ijkl-mnop \
  uv run python -c "from apple_calendar_mcp.caldav_client import get_client; print([c['name'] for c in get_client().list_calendars()])"
```

## 3. Connect it to Claude

### Claude Code (CLI)

```bash
claude mcp add apple-calendar \
  --env ICLOUD_USERNAME=you@icloud.com \
  --env ICLOUD_APP_PASSWORD=abcd-efgh-ijkl-mnop \
  --env CALDAV_DEFAULT_TZ=America/New_York \
  -- uv --directory /Users/edgargonzalez/apple-calendar-mcp run apple-calendar-mcp
```

Then in Claude Code the tools appear as `mcp__apple-calendar__list_events`, etc.

### Claude Desktop

Add to `claude_desktop_config.json`
(`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "apple-calendar": {
      "command": "uv",
      "args": [
        "--directory", "/Users/edgargonzalez/apple-calendar-mcp",
        "run", "apple-calendar-mcp"
      ],
      "env": {
        "ICLOUD_USERNAME": "you@icloud.com",
        "ICLOUD_APP_PASSWORD": "abcd-efgh-ijkl-mnop",
        "CALDAV_DEFAULT_TZ": "America/New_York"
      }
    }
  }
}
```

Restart Claude Desktop. The connector shows up under the tools/plug icon.

## 4. Usage examples (just ask Claude)

- "What's on my calendar next week?"
- "Create a 30-minute 'Dentist' event on my Home calendar next Tuesday at 3pm."
- "Find me a free hour for a call with alice@example.com between Monday and Wednesday."
- "Decline the 'All hands' invite."

## Notes & limits

- **app-specific password required** — a normal Apple password will fail with a 401.
- iCloud CalDAV does not send email invitations when you add attendees the way Google does;
  attendees are written to the event, but iCloud's server-side invite delivery is limited
  for third-party CalDAV clients.
- `recurrence` takes a raw RRULE string, e.g. `FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10`.
- Event ids are iCalendar UIDs.
- Reminders/Tasks calendars (VTODO) are skipped; only event calendars are returned.
