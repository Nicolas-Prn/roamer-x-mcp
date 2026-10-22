"""Command line: `roamer-x-mcp login | install | uninstall | status | serve`."""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, auth, config
from .client import Ibss, IbssError

NAME = "roamer-x"
REPO = "git+https://github.com/Nicolas-Prn/roamer-x-mcp"

# Cheapest call that needs a valid user token.
PROBE = "v2/1/bookings/my-bookings"


# ---------------------------------------------------------------- server command registered in clients

def server_command() -> list[str]:
    """Absolute command AI clients should launch (desktop apps do not inherit the shell's PATH)."""
    exe = shutil.which("roamer-x-mcp") or sys.argv[0]
    exe = str(Path(exe).resolve()) if exe and Path(exe).exists() else exe
    ephemeral = exe and re.search(r"[/\\](\.cache|Caches|cache)[/\\]uv[/\\]", exe)
    if not exe or ephemeral:
        uvx = shutil.which("uvx")
        if uvx:
            return [uvx, "--from", REPO, "roamer-x-mcp", "serve"]
    return [exe, "serve"]


# ---------------------------------------------------------------- AI clients

def _desktop_config() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def _json_merge(path: Path, cmd: list[str] | None) -> str:
    """Add (cmd) or remove (None) our entry in a `{"mcpServers": {...}}` JSON config file."""
    data = json.loads(path.read_text() or "{}") if path.exists() else {}
    servers = data.setdefault("mcpServers", {})
    if cmd is None:
        if servers.pop(NAME, None) is None:
            return "not registered"
    else:
        servers[NAME] = {"command": cmd[0], "args": cmd[1:]}
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return f"updated {path}"


def _codex(cmd: list[str] | None) -> str:
    path = Path.home() / ".codex/config.toml"
    text = path.read_text() if path.exists() else ""
    # Drop our previous table (up to the next table header), then append the new one.
    text = re.sub(rf'(?ms)^\[mcp_servers\."?{re.escape(NAME)}"?\]\n.*?(?=^\[|\Z)', "", text).rstrip()
    if cmd is not None:
        args = ", ".join(json.dumps(a) for a in cmd[1:])
        text += f'\n\n[mcp_servers."{NAME}"]\ncommand = {json.dumps(cmd[0])}\nargs = [{args}]\n'
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.lstrip() + ("\n" if text and not text.endswith("\n") else ""))
    return f"updated {path}"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _claude_code(cmd: list[str] | None) -> str:
    claude = shutil.which("claude")
    _run([claude, "mcp", "remove", "--scope", "user", NAME])
    if cmd is None:
        return "removed (user scope)"
    res = _run([claude, "mcp", "add", "--scope", "user", NAME, "--", *cmd])
    if res.returncode:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip())
    return "registered for all projects (user scope)"


def _openclaw(cmd: list[str] | None) -> str:
    openclaw = shutil.which("openclaw")
    if cmd is None:
        res = _run([openclaw, "mcp", "unset", NAME])
        return "removed" if res.returncode == 0 else "not registered"
    res = _run([openclaw, "mcp", "set", NAME, json.dumps({"command": cmd[0], "args": cmd[1:]})])
    if res.returncode:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip())
    _run([openclaw, "mcp", "reload"])
    return "registered (openclaw mcp set)"


CLIENTS = {
    "claude-code": ("Claude Code", lambda: shutil.which("claude"), _claude_code),
    "claude-desktop": ("Claude Desktop", lambda: _desktop_config().parent.exists(),
                       lambda cmd: _json_merge(_desktop_config(), cmd)),
    "codex": ("Codex", lambda: (Path.home() / ".codex").exists() or shutil.which("codex"), _codex),
    "cursor": ("Cursor", lambda: (Path.home() / ".cursor").exists(),
               lambda cmd: _json_merge(Path.home() / ".cursor/mcp.json", cmd)),
    "gemini": ("Gemini CLI", lambda: (Path.home() / ".gemini").exists(),
               lambda cmd: _json_merge(Path.home() / ".gemini/settings.json", cmd)),
    "windsurf": ("Windsurf", lambda: (Path.home() / ".codeium/windsurf").exists(),
                 lambda cmd: _json_merge(Path.home() / ".codeium/windsurf/mcp_config.json", cmd)),
    "openclaw": ("OpenClaw", lambda: shutil.which("openclaw"), _openclaw),
}


def _ask(question: str, default: bool = True) -> bool:
    answer = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not answer else answer.startswith("y")


def install(names: list[str] | None, yes: bool, remove: bool = False) -> None:
    cmd = None if remove else server_command()
    chosen = names or [k for k, (_, detect, _) in CLIENTS.items() if detect()]
    if not chosen:
        print("No supported AI client detected.")
    for key in chosen:
        if key not in CLIENTS:
            print(f"  ? unknown client {key!r} (known: {', '.join(CLIENTS)})")
            continue
        label, _, apply = CLIENTS[key]
        if not yes and not _ask(f"{'Remove from' if remove else 'Add to'} {label}?"):
            continue
        try:
            print(f"  ✓ {label}: {apply(cmd)}")
        except Exception as e:
            print(f"  ✗ {label}: {e}")
    if cmd:
        print("\nRestart Claude Desktop / Cursor / other open apps to load the server.")
        print("Any other MCP client: add a stdio server with this command:")
        print("  " + " ".join(f'"{a}"' if " " in a else a for a in cmd))


# ---------------------------------------------------------------- commands

def cmd_login(args: argparse.Namespace) -> int:
    if not auth.can_open_window():
        print("No display: `login` needs a browser window. Run it from a desktop session, or over SSH with\n"
              "X forwarding (`ssh -X host`, XQuartz on macOS), then run it again.", file=sys.stderr)
        return 1
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
    print(f"✓ Signed in. Token saved to {config.CONFIG_FILE}; it is renewed automatically from now on.")
    if not args.no_install:
        print("\nRegister the server in your AI apps:")
        install(None, args.yes)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print(f"roamer-x-mcp {__version__}\nconfig: {config.CONFIG_FILE}")
    ok = True
    try:
        Ibss(config.load()).get(PROBE, **{"$top": 1})
        settings = config.load()
        minutes = int((auth.token_expiry(settings.token) - time.time()) / 60)
        print(f"✓ {settings.api_url}: signed in as {auth.email_of(settings.token)} (token valid for {minutes} min)")
    except (config.NotConfiguredError, IbssError) as e:
        print(f"✗ {e}")
        ok = False
    print("server command: " + " ".join(server_command()))
    for key, (label, detect, _) in CLIENTS.items():
        print(f"  {label:15} {'detected' if detect() else '-'}")
    return 0 if ok else 1


def cmd_install(args: argparse.Namespace) -> int:
    install(args.clients, args.yes, remove=args.command == "uninstall")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import run
    run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="roamer-x-mcp", description="MCP server for Roamer X (ICONICS IBSS).")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("login", help="sign in through the IBSS Hub, then register the server in your AI apps")
    p.add_argument("--tenant", help=f"IBSS tenant or hub URL (default: {config.DEFAULT_TENANT})")
    p.add_argument("--no-install", action="store_true", help="only sign in")
    p.add_argument("-y", "--yes", action="store_true", help="register in every detected app without asking")
    p.set_defaults(fn=cmd_login)
    for name, help_ in (("install", "register the server in AI apps"), ("uninstall", "unregister it")):
        i = sub.add_parser(name, help=help_)
        i.add_argument("clients", nargs="*", help=f"any of: {', '.join(CLIENTS)} (default: all detected)")
        i.add_argument("-y", "--yes", action="store_true")
        i.set_defaults(fn=cmd_install)
    sub.add_parser("status", help="check the connection and show detected apps").set_defaults(fn=cmd_status)
    sub.add_parser("serve", help="run the MCP server (stdio); AI clients launch this").set_defaults(fn=cmd_serve)

    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        if not sys.stdin.isatty():
            return cmd_serve(args)
        parser.print_help()
        return 0
    try:
        return args.fn(args)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
