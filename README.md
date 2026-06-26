# Apple Calendar MCP connector

A Claude **connector** (MCP server) for **Apple Calendar / iCloud**, built to mirror the
Google Calendar connector. It exposes the same tools so Claude can manage your iCloud
calendar the same way it manages Google Calendar:

> **Want to use this for your own Apple ID?** Don't reuse someone else's connector URL —
> that URL is tied to *their* iCloud account. Deploy your own free copy in ~5 minutes:
> see **[Deploy your own copy](#deploy-your-own-copy-free-no-credit-card)** below.

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

## Deploy your own copy (free, no credit card)

Run your own instance against **your** Apple ID. You never touch anyone else's calendar,
and they never touch yours.

1. **Get an iCloud app-specific password** — sign in at <https://account.apple.com> →
   **Sign-In and Security** → **App-Specific Passwords** → generate one (looks like
   `abcd-efgh-ijkl-mnop`). Apple blocks your normal password for this.

2. **Make a free Hugging Face account** at <https://huggingface.co/join> (no credit card).

3. **Duplicate this Space** — open
   <https://huggingface.co/spaces/edg45/apple-calendar-mcp?duplicate=true> and click
   **Duplicate Space**. (Set it **Private** if you like; it still works.)

4. **Add your 4 secrets** — in your new Space: **Settings → Variables and secrets →
   New secret**, add each as a *Secret*:

   | Name | Value |
   |------|-------|
   | `ICLOUD_USERNAME` | your Apple ID email |
   | `ICLOUD_APP_PASSWORD` | the app-specific password from step 1 |
   | `MCP_URL_SECRET` | a long random string you make up (this is your URL password) |
   | `CALDAV_DEFAULT_TZ` | your timezone, e.g. `America/New_York` |

   The Space rebuilds automatically.

5. **Add it to Claude** — once the Space shows **Running**, your connector URL is:

   ```
   https://<your-hf-username>-apple-calendar-mcp.hf.space/<MCP_URL_SECRET>/mcp
   ```

   In Claude: **Settings → Connectors → Add custom connector** → paste that URL → **Add**.
   Leave the OAuth fields blank.

**Treat your connector URL like a password** — anyone who has it can read/write your
calendar. The `MCP_URL_SECRET` in the path is what keeps it private.

## Notes & limits

- **app-specific password required** — a normal Apple password will fail with a 401.
- iCloud CalDAV does not send email invitations when you add attendees the way Google does;
  attendees are written to the event, but iCloud's server-side invite delivery is limited
  for third-party CalDAV clients.
- `recurrence` takes a raw RRULE string, e.g. `FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10`.
- Event ids are iCalendar UIDs.
- Reminders/Tasks calendars (VTODO) are skipped; only event calendars are returned.
