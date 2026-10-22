# roamer-x-mcp

> Early version: sign-in, search, booking and cancellation are verified on the ULB/VUB tenant.

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

**1. Install [uv](https://docs.astral.sh/uv/)** if you do not have it:
`curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS / Linux), then open a new terminal.

**2. Install roamer-x-mcp and sign in**

```sh
uv tool install git+https://github.com/Nicolas-Prn/roamer-x-mcp
roamer-x-mcp login
```

`login` opens a browser window on the IBSS Hub where you sign in with your university account (Microsoft),
then offers to register the server in every AI app it finds: Claude Code, Claude Desktop, Codex, Cursor,
Gemini CLI, Windsurf and [OpenClaw](https://openclaw.ai). Restart open desktop apps afterwards.

- The session is kept in a dedicated browser profile under `~/.config/roamer-x-mcp/`. The API token lasts
  about an hour; the server renews it in the background by reopening the Hub headless, and signs in again
  silently with Microsoft's "stay signed in" cookie when the Hub session has expired. Your password is never
  seen or stored. Run `login` again if Microsoft asks for your password or MFA.
- Browser: your installed Google Chrome, else a system Chromium (`/usr/bin/chromium`, e.g. on a Raspberry
  Pi), else Playwright's (`uvx playwright install chromium`).
- Headless machine (server, Raspberry Pi): `login` needs a window once. Run it over SSH with X forwarding
  (`ssh -X host`, with XQuartz on macOS) or from the machine's desktop session.

Other commands: `roamer-x-mcp install [client…]` / `uninstall [client…]` to (un)register the server,
`roamer-x-mcp status` to check the connection and see detected apps.

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
