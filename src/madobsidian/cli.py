"""Command-line interface for madObsidian."""

import argparse

from madobsidian import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="madObsidian",
        description="Personal media archive and Obsidian integration.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"madObsidian {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="Show the application status.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "status":
        print(f"madObsidian {__version__}: ready (no archive configured)")
        return 0

    # With no subcommand, argparse has already displayed help only when asked.
    # Give an actionable default for a bare invocation.
    build_parser().print_help()
    return 0
