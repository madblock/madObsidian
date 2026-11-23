"""Command-line interface for madObsidian."""

import argparse
import difflib
import sys
from pathlib import Path

from madobsidian import __version__
from madobsidian.archive import ArchiveError, rebuild_index, scan_archive
from madobsidian.config import ConfigError, load_config
from madobsidian.importer import ImportError, _apply_markdown_edits, import_path, import_vault


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
    import_parser.add_argument("source", type=Path, help="A vault directory, or legacy file/directory source.")
    import_parser.add_argument("archive", type=Path, nargs="?", help="Archive directory for legacy import mode.")
    import_parser.add_argument("--dry-run", action="store_true", help="Preview vault import without changing files.")
    import_parser.add_argument("--config", type=Path, help="INI processing configuration file.")
    verify_parser = subparsers.add_parser("verify", help="Verify archive objects against their metadata.")
    verify_parser.add_argument("archive", type=Path, help="The archive directory.")
    verify_parser.add_argument("--config", type=Path, help="INI processing configuration file.")
    rebuild_parser = subparsers.add_parser("rebuild-index", help="Rebuild the archive index from object metadata.")
    rebuild_parser.add_argument("archive", type=Path, help="The archive directory.")
    rebuild_parser.add_argument("--config", type=Path, help="INI processing configuration file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "status":
        print(f"madObsidian {__version__}: ready (no archive configured)")
        return 0
    if args.command == "import":
        try:
            if args.archive is not None and args.dry_run:
                raise ImportError("--dry-run is only supported for vault imports; legacy copy mode always imports")
            config = load_config(args.config)
            if args.archive is None:
                rows = import_vault(args.source, dry_run=args.dry_run, config=config)
                print(
                    f"{'Would import' if args.dry_run else 'Imported'} {len(rows)} file(s); "
                    f"skipped {len(rows.skipped)}; ignored {len(rows.ignored)}; "
                    f"failed {len(rows.failures)}."
                )
                for row in rows:
                    target_text = f" -> {row['target']}" if row.get("target") else ""
                    print(f"{row['id']}  {row['source']}{target_text}")
                    if row.get("action"):
                        print(f"  Recovery: {row['action']}")
                    for ambiguity in row.get("ambiguities", []):
                        candidates = ", ".join(ambiguity["candidates"])
                        print(f"  Ambiguous reference in {ambiguity['note']}: {ambiguity['reference']} (matches: {candidates})")
                    for note, content in row["changes"].items():
                        print(f"  Markdown: {note}")
                        if args.dry_run:
                            with (args.source / note).open("r", encoding="utf-8", newline="") as stream:
                                old_text = stream.read()
                            new_text = _apply_markdown_edits(old_text, content, note)
                            old_text = old_text.splitlines(keepends=True)
                            new_text = new_text.splitlines(keepends=True)
                            print("".join(difflib.unified_diff(old_text, new_text, fromfile=note, tofile=note + " (proposed)")), end="")
                for skipped in rows.skipped:
                    print(f"Skipped: {skipped['source']} ({skipped['reason']})")
                for ignored in rows.ignored:
                    print(f"Ignored: {ignored['source']} ({ignored['reason']})")
                for failure in rows.failures:
                    severity = "recovery error" if failure["serious"] else "failed"
                    print(f"{severity.title()}: {failure['source']} ({failure['reason']})", file=sys.stderr)
                if rows.failures:
                    return 1
                return 0
            results = import_path(args.source, args.archive, config=config)
        except (ConfigError, ImportError, OSError) as error:
            print(f"Import failed: {error}", file=sys.stderr)
            return 1
        print(f"Imported {len(results)} file(s).")
        for result in results:
            print(f"{result.metadata.id}  {result.metadata.original_filename}")
        return 0
    if args.command in {"verify", "rebuild-index"}:
        try:
            config = load_config(args.config)
            options = {"objects_directory": config.general.objects_directory,
                       "small_file_threshold_bytes": config.general.small_file_threshold_bytes}
            report = scan_archive(args.archive, **options) if args.command == "verify" else rebuild_index(args.archive, **options)
        except (ConfigError, ArchiveError, OSError) as error:
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
