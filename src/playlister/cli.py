"""Console entry point. Default transport is stdio; HTTP stays on localhost."""

from __future__ import annotations

import argparse
import os

from playlister import __version__
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(os.environ.get("PLAYLISTER_LOG_LEVEL", "INFO"))
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
