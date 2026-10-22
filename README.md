# roamer-x-mcp

> **Work in progress.** Sign-in, search, booking and cancellation work (verified on the ULB/VUB tenant);
> client registration (`install`) is still to do.

Let your AI assistant book rooms for you through **Roamer X**, the app ULB and VUB use for the
[LIC building](https://www.lic.brussels/book-a-space). Ask things like:

> *Book me a meeting room at the LIC tomorrow from 14:00 to 16:00.*
> *What have I booked this week?*
> *Cancel my Thursday booking.*

`roamer-x-mcp` is an [MCP](https://modelcontextprotocol.io) server: a small program that AI apps (Claude Code,
Claude Desktop, Codex, Cursor…) launch in the background to get extra tools. It talks to the ICONICS IBSS API
behind Roamer X with **your own** session, and can only do what you can do in the app.

Made for the ULB/VUB tenant (`uob`, the default), but it should work with any IBSS tenant
(`roamer-x-mcp login --tenant <name>`).

## Install

```sh
uv tool install git+https://github.com/Nicolas-Prn/roamer-x-mcp
roamer-x-mcp login
```

`login` opens a browser window on the IBSS Hub where you sign in with your university account (Microsoft).
The session is kept in a dedicated browser profile under `~/.config/roamer-x-mcp/`; the server renews the
API token (valid about an hour) by reopening the Hub in the background. Your password is never seen or
stored. It uses your installed Google Chrome, or Playwright's Chromium (`uvx playwright install chromium`).
Run `login` again if the Microsoft session expires.

## Tools

| Tool | What it does |
|---|---|
| `list_locations` | Buildings, floors and searchable space types |
| `search_spaces` | Spaces free for a whole slot (type, floor, capacity), with their booking rules |
| `get_space` | Details and booking rules of one space |
| `list_my_bookings` | Your bookings over a period |
| `book_space` | Books a space in your name (checks the rules first) |
| `cancel_booking` | Cancels one of your bookings |
| `connection_status` | Checks the connection |

## Development

```sh
uv sync
uv run pytest
uv run roamer-x-mcp status
```

How the IBSS API works and which endpoints are used: see [docs/api.md](docs/api.md).

## License

MIT
