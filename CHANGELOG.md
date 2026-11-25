# Changelog

All notable changes to madObsidian are documented in this file.

History prior to the creation of this changelog is reconstructed strictly from Git commit history (`git log`). Dates reflect commit timestamps in the repository. Note that no Git release tags currently exist for historical releases (`0.1.0`, `0.2.0`, `0.2.1`).

## [Unreleased]

## [0.2.2] - 2026-11-25

*Prepared release candidate on `master` (not yet committed or tagged; annotated `v0.2.2` tag planned on final release commit).*

### Added

- Optional `[tools]` configuration section (`ffmpeg = auto`, `ffprobe = auto`) in `src/madobsidian/config.py` and `config.example.ini`, with independent per-executable precedence (`MADOBSIDIAN_FFMPEG` / `MADOBSIDIAN_FFPROBE` → explicit `[tools]` value → `PATH`), lazy `auto` `PATH` discovery, fail-closed explicit validation (relative paths resolved from `config_path.parent`, absolute paths, bare names via `shutil.which()`, POSIX `os.X_OK` check) without silent fallback, wired consistently across `import`, `verify`, and `rebuild-index`.
- Dedicated `ImportBatchResult.recoveries` attribute separating pending recovery operations from planned dry-run imports (`len(batch)` excludes recoveries), with separate CLI dry-run summary counts (`pending recoveries N`) and `Pending recovery:` output lines.
- End-to-end regression test in `tests/test_exclusions.py` covering pre-hashing `_sha256` `OSError` preservation, unchanged Markdown, non-serious failure reporting, `[ignore]` persistence, no published object, and skip-before-hash on rerun.
- Comprehensive unit tests in `tests/test_config.py` and `tests/test_vault_import.py` covering `[tools]` precedence/validation/CLI integration, `watermark.enabled = false` font-validation skipping, and `batch.recoveries` dry-run vs. real-import semantics.

### Changed

- Skipped custom font existence and Pillow loading checks in `load_config()` when `[watermark] enabled = false`, while preserving strict validation when watermarking is enabled.
- Updated package and CLI version strings to `0.2.2` across `pyproject.toml`, `src/madobsidian/__init__.py`, and `tests/test_cli.py`.

### Fixed

- Fixed stale `return_value=[]` mock in `tests/test_config.py::ConfigTests::test_vault_cli_config_option_is_forwarded` to return `ImportBatchResult()`, restoring full 12-module test suite execution.

### Documentation

- Established durable engineering documentation in `docs/` (`SPECIFICATION.md`, `DECISIONS.md`, `PROGRESS.md`, `ACCEPTANCE_CRITERIA.md`, `IMPLEMENTATION_PLAN.md`) and added `CHANGELOG.md`.
- Streamlined `README.md` as the project entry point with links to the engineering documentation and updated `AGENTS.md` with automatic documentation-synchronization rules.

## [0.2.1] - 2026-11-23

*Commits: `53d1fd6`, `06e76c3` (two commits pushed to `master`; no Git tag created).*

### Added

- Persistent failed-import exclusions in `exclusions.ini` (`src/madobsidian/exclusions.py`), using an `[ignore]` section keyed by SHA-256 of the exact UTF-8 filename with JSON-encoded filename values, fail-closed validation, snapshot verification, and cross-process atomic replacement via `exclusions.ini.lock`.
- Cross-platform per-vault importer serialization lock (`src/madobsidian/locking.py`) placed in `vault.parent` as `.madobsidian-vault-<sha256>.lock` using `msvcrt.locking` on Windows and `fcntl.flock` on POSIX.
- Separate CLI reporting for ignored files (`ImportBatchResult.ignored` and `Ignored:` lines in `src/madobsidian/cli.py`).
- `.gitignore` entries for `exclusions.ini`, `exclusions.ini.lock`, and `.madobsidian-vault-*.lock`.
- Focused test suites in `tests/test_exclusions.py` and `tests/test_import_locking.py`, plus expanded failure reporting tests in `tests/test_import_failures.py`.

### Changed

- Synchronized `pyproject.toml`, `src/madobsidian/__init__.py`, and `tests/test_cli.py` to version `0.2.1` (previously `src/madobsidian/__init__.py` and `tests/test_cli.py` had remained at `0.1.0` while `pyproject.toml` was `0.2.0`).
- Refined `report_failure` verification error message in `src/madobsidian/importer.py` to remove the inaccurate `"after rollback"` suffix for pre-transaction failures.
- Updated `README.md` to document persistent exclusions, per-vault locking, and removed the completed locking item from `TODO`.

## [0.2.0] - 2026-11-22

*Commits: `00269a7`, `f2c0577`, `b68778d`, `7d3621b`, `27055dd` (version bumped in `pyproject.toml` in `27055dd`; `src/madobsidian/__init__.py` remained `0.1.0`; no Git tag created).*

### Added

- Vault-mode importer (`import_vault`) with move-only same-filesystem staging (`Path.rename`), `shared/` vs. `originals/` size-threshold layout, `--dry-run` unified Markdown diff preview, and Obsidian wikilink/embed rewriting with `madobsidian://<UUID>` original links for large media.
- Transaction journal (`transaction.json`) and idempotent crash recovery (`recover_transactions`) supporting both pre-publication rollback (hard-link or exclusive copy restoration to `Incoming/`) and post-publication Markdown edit replay.
- Centralized, fail-closed INI configuration (`src/madobsidian/config.py`) and portable `config.example.ini` (included in distributions via `MANIFEST.in`).
- Referenced-media filtering (`7d3621b`): vault imports process only direct, non-hidden, supported-extension media files referenced in vault Markdown (or participating in an ambiguous reference).
- Per-file failure isolation, rollback verification, continuation, nonzero CLI exit on partial batch failure, and structured JSONL error logging to `import.log` beside the vault (`27055dd`, `tests/test_import_failures.py`).
- Multiline watermark text wrapping for Pillow layout engines (`tests/test_watermark_wrap.py`, `27055dd`).
- Archive verification support for vault layout (`shared/` / `originals/`), preview decoding (WebP and H.264 MP4 via FFmpeg/ffprobe), and rejection of unfinished `transaction.json` journals.
- Regression check ensuring obsolete `failed-imports list` CLI subcommand is not exposed (`tests/test_cli.py`).

### Changed

- Simplified importer error logging to write JSONL records directly beside the vault (`f2c0577`).
- Documented Syncthing `.stignore` recommendations for multi-device deployments (Windows, Raspberry Pi, VPS, Android).

## [0.1.0] - 2026-11-15 to 2026-11-16

*Commits: `8ace126`, `eac5049`, `154b26a`, `6327e70`, `6e48ec4` (no Git tag created).*

### Added

- Initial Python package skeleton (`pyproject.toml`, `madObsidian` CLI entry point, `--version`, `--help`, `status` placeholder).
- Copy-only verified media importer (`import_path`) creating UUID object directories with SHA-256 verification and authoritative `metadata.json`.
- Cross-platform archived filename validation (`src/madobsidian/filenames.py`).
- Archive verification (`verify` / `scan_archive`) and disposable index rebuilding (`rebuild-index` / `rebuild_index`) in `src/madobsidian/archive.py`.
- Watermarked WebP image preview generation (`src/madobsidian/previews.py`) using Pillow (`>=10.0`) with EXIF orientation, transparency preservation, and bundled DejaVu Sans 2.37 font (`src/madobsidian/assets/`).
- Full-length H.264/AAC watermarked MP4 video preview generation (`src/madobsidian/video_previews.py`) with VFR timing and audio/video synchronization verification via `ffmpeg` and `ffprobe`.
