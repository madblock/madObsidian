"""Command-line interface for madObsidian."""

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

from madobsidian import __version__
from madobsidian.archive import ArchiveError, rebuild_index, scan_archive
from madobsidian.config import ConfigError, load_config, resolve_incoming, resolve_vault_directory
from madobsidian.exclusions import exclusion_path, load_exclusions
from madobsidian.video_previews import _executable
from madobsidian.importer import ImportError, _apply_markdown_edits, import_vault


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
    status = subparsers.add_parser("status", help="Show read-only vault diagnostics.")
    status.add_argument("vault", nargs="?", type=Path)
    status.add_argument("--config", type=Path)
    import_parser = subparsers.add_parser("import", help="Import files into an archive.")
    import_parser.add_argument("source", type=Path, help="A vault directory.")
    import_parser.add_argument("--dry-run", action="store_true", help="Preview vault import without changing files.")
    import_parser.add_argument("--config", type=Path, help="INI processing configuration file.")
    verify_parser = subparsers.add_parser("verify", help="Verify archive objects against their metadata.")
    verify_parser.add_argument("archive", type=Path, help="The archive directory.")
    verify_parser.add_argument("--config", type=Path, help="INI processing configuration file.")
    rebuild_parser = subparsers.add_parser("rebuild-index", help="Rebuild the archive index from object metadata.")
    rebuild_parser.add_argument("archive", type=Path, help="The archive directory.")
    rebuild_parser.add_argument("--config", type=Path, help="INI processing configuration file.")
    return parser


def _status(vault: Path | None, config_path: Path | None) -> int:
    print(f"madObsidian {__version__}")
    print("Configuration:", config_path.resolve() if config_path else "built-in defaults")
    warnings = []
    if vault is not None:
        print("Vault:", vault.resolve())
    try:
        config = load_config(config_path)
        for tool in ("ffmpeg", "ffprobe"):
            try:
                location = _executable("MADOBSIDIAN_" + tool.upper(), tool, getattr(config.tools, tool))
            except OSError as error:
                location = f"unavailable ({error})"
            print(f"{tool}: {location}")
        if vault is None:
            print("Vault: unspecified")
            return 0
        root = vault.resolve()
        if not root.is_dir():
            raise ConfigError(f"Missing vault directory: {root}")
        try:
            incoming = resolve_incoming(root, config)
            print("Incoming:", incoming, "(present)" if incoming.is_dir() else "(missing)")
        except ConfigError as error:
            warnings.append(str(error))
        objects = resolve_vault_directory(root, config.general.objects_directory)
        print("Archive:", objects, "(present)" if objects.is_dir() else "(missing)")
        index = root / "index.json"
        health = "missing"
        if index.is_symlink():
            health = "unsafe symbolic link"
        elif index.is_file():
            if index.stat().st_size <= 1024 * 1024:
                try:
                    with index.open("rb") as stream:
                        data = json.loads(stream.read(1024 * 1024 + 1))
                    health = ("readable" if isinstance(data, dict) and data.get("version") == 1
                              and isinstance(data.get("objects"), list) else "unexpected format")
                except (OSError, ValueError, RecursionError) as error:
                    health = "unreadable"
                    warnings.append(f"{index}: {error}")
            else:
                health = "present (exceeds diagnostic read limit)"
        print("Index:", health)
        pending = 0
        truncated = False
        if objects.is_dir():
            with os.scandir(objects) as entries:
                for count, entry in enumerate(entries):
                    if count >= 1000:
                        truncated = True
                        break
                    if entry.name.startswith(".incomplete-") or (
                        entry.is_dir(follow_symlinks=False) and (Path(entry.path) / "transaction.json").exists()
                    ):
                        pending += 1
        print("Pending recovery operations:", pending, "(partial count)" if truncated else "")
        exclusions = exclusion_path(root)
        print("Exclusions:", exclusions)
        print("Exclusion entries:", len(load_exclusions(exclusions)))
        for conflict in root.glob("*.sync-conflict-*"):
            warnings.append(f"Syncthing conflict: {conflict.name}")
    except (ValueError, OSError) as error:
        warnings.append(str(error))
    for warning in warnings:
        print("Diagnostic warning:", warning, file=sys.stderr)
    return int(bool(warnings))


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "status":
        return _status(args.vault, args.config)
    if args.command == "import":
        try:
            config = load_config(args.config)
            rows = import_vault(args.source, dry_run=args.dry_run, config=config)
            if args.dry_run:
                print(
                    f"Would import {len(rows)} file(s); "
                    f"pending recoveries {len(rows.recoveries)}; "
                    f"skipped {len(rows.skipped)}; ignored {len(rows.ignored)}; "
                    f"failed {len(rows.failures)}."
                )
            else:
                print(
                    f"Imported {len(rows)} file(s); "
                    f"skipped {len(rows.skipped)}; ignored {len(rows.ignored)}; "
                    f"failed {len(rows.failures)}."
                )
            for recovery in rows.recoveries:
                print(f"Pending recovery: {recovery['id']}  {recovery['source']} ({recovery['action']})")
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
        except (ConfigError, ImportError, OSError) as error:
            print(f"Import failed: {error}", file=sys.stderr)
            return 1
    if args.command in {"verify", "rebuild-index"}:
        try:
            config = load_config(args.config)
            options = {"objects_directory": config.general.objects_directory,
                       "small_file_threshold_bytes": config.general.small_file_threshold_bytes,
                       "tools": config.tools}
            report = scan_archive(args.archive, **options) if args.command == "verify" else rebuild_index(args.archive, **options)
        except (ConfigError, ArchiveError, OSError) as error:
            print(f"Archive operation failed: {error}", file=sys.stderr)
            return 1
        for problem in report.problems:
            print(f"Problem: {problem}", file=sys.stderr)
        for obj in report.objects:
            print(f"Valid: {obj.metadata['id']}  {obj.metadata['original_filename']}")
        if report.problems:
            print("Local archive verification incomplete.", file=sys.stderr)
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
