"""Command-line interface for madObsidian."""

import argparse
import sys
from pathlib import Path

from madobsidian import __version__
from madobsidian.archive import ArchiveError, rebuild_index, scan_archive
from madobsidian.importer import ImportError, import_path


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
    import_parser = subparsers.add_parser("import", help="Import files into an archive.")
    import_parser.add_argument("source", type=Path, help="A file or directory to import.")
    import_parser.add_argument("archive", type=Path, help="The archive directory.")
    verify_parser = subparsers.add_parser("verify", help="Verify archive objects against their metadata.")
    verify_parser.add_argument("archive", type=Path, help="The archive directory.")
    rebuild_parser = subparsers.add_parser("rebuild-index", help="Rebuild the archive index from object metadata.")
    rebuild_parser.add_argument("archive", type=Path, help="The archive directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "status":
        print(f"madObsidian {__version__}: ready (no archive configured)")
        return 0
    if args.command == "import":
        try:
            results = import_path(args.source, args.archive)
        except (ImportError, OSError) as error:
            print(f"Import failed: {error}", file=sys.stderr)
            return 1
        print(f"Imported {len(results)} file(s).")
        for result in results:
            print(f"{result.metadata.id}  {result.metadata.original_filename}")
        return 0
    if args.command in {"verify", "rebuild-index"}:
        try:
            report = scan_archive(args.archive) if args.command == "verify" else rebuild_index(args.archive)
        except (ArchiveError, OSError) as error:
            print(f"Archive operation failed: {error}", file=sys.stderr)
            return 1
        for problem in report.problems:
            print(f"Problem: {problem}", file=sys.stderr)
        for obj in report.objects:
            print(f"Valid: {obj.metadata['id']}  {obj.metadata['original_filename']}")
        if report.problems:
            if args.command == "rebuild-index":
                print("Index was not replaced because archive problems were found.", file=sys.stderr)
            return 1
        if args.command == "verify":
            print(f"Verified {len(report.objects)} object(s); no problems found.")
        else:
            print(f"Rebuilt index with {len(report.objects)} object(s).")
        return 0

    # With no subcommand, argparse has already displayed help only when asked.
    # Give an actionable default for a bare invocation.
    build_parser().print_help()
    return 0
