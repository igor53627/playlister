"""Console entry point. Default transport is stdio; HTTP stays on localhost."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from playlister import __version__
from playlister.auth import AuthError, default_auth_path
from playlister.oauth import run_device_flow
from playlister.redact import configure_logging
from playlister.server import TOOL_NAMES, run_http, run_stdio

LOCALHOST = "127.0.0.1"
DEFAULT_PORT = 8765


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="playlister",
        description=(
            "Local MCP server that turns a chat tracklist into a YouTube Music playlist. "
            "Stdio is the default. HTTP listens on 127.0.0.1 only."
        ),
    )
    parser.add_argument("--version", action="version", version=f"playlister {__version__}")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio (default) or streamable HTTP on localhost",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PLAYLISTER_HTTP_PORT", DEFAULT_PORT)),
        help=f"localhost port for --transport http (default {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="print tool names and exit, without starting the server",
    )

    subparsers = parser.add_subparsers(dest="command")
    auth_parser = subparsers.add_parser("auth", help="set up local credentials")
    auth_sub = auth_parser.add_subparsers(dest="auth_command", required=True)
    oauth_parser = auth_sub.add_parser(
        "oauth",
        help="run Google OAuth device flow and save token JSON (mode 600)",
    )
    oauth_parser.add_argument(
        "--file",
        "-f",
        default=None,
        help=(
            "destination token path "
            "(defaults to PLAYLISTER_AUTH_PATH or ~/.config/playlister/auth.json)"
        ),
    )
    return parser.parse_args(argv)


def _resolve_oauth_client_creds() -> tuple[str, str]:
    client_id = os.environ.get("PLAYLISTER_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("PLAYLISTER_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id and sys.stdin.isatty():
        client_id = input("Google OAuth Client ID: ").strip()
    if not client_secret and sys.stdin.isatty():
        client_secret = getpass.getpass("Google OAuth Client Secret: ").strip()
    if not client_id or not client_secret:
        raise AuthError(
            "Set PLAYLISTER_OAUTH_CLIENT_ID and PLAYLISTER_OAUTH_CLIENT_SECRET "
            "in your environment before running `playlister auth oauth`."
        )
    return client_id, client_secret


def _run_auth_oauth(file_arg: str | None) -> int:
    target = Path(file_arg).expanduser() if file_arg else default_auth_path()
    try:
        client_id, client_secret = _resolve_oauth_client_creds()
        saved = run_device_flow(target, client_id=client_id, client_secret=client_secret)
    except AuthError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    print(f"Saved OAuth token to {saved} (mode 600).")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(os.environ.get("PLAYLISTER_LOG_LEVEL", "INFO"))
    if args.command == "auth" and args.auth_command == "oauth":
        return _run_auth_oauth(args.file)
    if args.list_tools:
        for name in TOOL_NAMES:
            print(name)
        return 0
    if args.transport == "http":
        if args.port < 1 or args.port > 65535:
            raise SystemExit("port must be between 1 and 65535")
        run_http(args.port)
        return 0
    run_stdio()
    return 0
