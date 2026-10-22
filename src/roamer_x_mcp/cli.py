"""Command line: `roamer-x-mcp login | status | serve`."""
from __future__ import annotations

import argparse
import sys
import time

from . import __version__, auth, config
from .client import Ibss, IbssError

# Cheapest call that needs a valid user token.
PROBE = "v2/1/bookings/my-bookings"


def cmd_login(args: argparse.Namespace) -> int:
    tenant = config.normalize_tenant(args.tenant or config.load(require_token=False).tenant)
    print("A browser window opens on the IBSS Hub: sign in with your university account, then wait.")
    try:
        token = auth.browser_token(tenant, interactive=True)
        settings = config.Settings(tenant, token)
        Ibss(settings, auto_refresh=False).get(PROBE, **{"$top": 1})
    except (auth.LoginRequiredError, IbssError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    config.save(settings)
    print(f"Signed in. Token saved to {config.CONFIG_FILE}; it is renewed automatically from now on.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    try:
        settings = config.load()
        client = Ibss(settings)
        client.get(PROBE, **{"$top": 1})
        settings = config.load()
    except (config.NotConfiguredError, IbssError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    minutes = int((auth.token_expiry(settings.token) - time.time()) / 60)
    print(f"Tenant {settings.tenant} ({settings.api_url}), OK (token valid for {minutes} min)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import run
    run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="roamer-x-mcp", description="MCP server for Roamer X (ICONICS IBSS).")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("login", help="sign in through the IBSS Hub in a browser window")
    p.add_argument("--tenant", help=f"IBSS tenant or hub URL (default: {config.DEFAULT_TENANT})")
    p.set_defaults(fn=cmd_login)
    sub.add_parser("status", help="check the connection").set_defaults(fn=cmd_status)
    sub.add_parser("serve", help="run the MCP server (stdio); AI clients launch this").set_defaults(fn=cmd_serve)

    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
