# Project Progress and Current State

**Current version:** `0.2.1` (branch `master`).
**Last audited:** 2026-11-25 (full 12-module test suite executed with `PYTHONPATH=src` and FFmpeg/ffprobe configured: **156/156 passing**).

This document tracks what is implemented, what is verified by tests, known bugs and discrepancies, technical debt, deferred work, and open questions.

---

## 1. Implemented Functionality

| Capability | Status | Primary Modules | Verification / Tests |
| --- | --- | --- | --- |
| Vault move-only importer (`import_vault`, `--dry-run`) | Implemented | `importer.py`, `cli.py` | `tests/test_vault_import.py` |
| Legacy copy-only importer (`import_path`) | Implemented | `importer.py`, `cli.py` | `tests/test_importer.py` |
| Transactional journaling (`transaction.json`) & crash recovery | Implemented | `importer.py` | `tests/test_vault_import.py`, `tests/test_import_failures.py` |
| Per-file failure isolation, rollback & JSONL `import.log` | Implemented | `importer.py` | `tests/test_import_failures.py`, `tests/test_vault_import.py` |
| Persistent failed-import exclusions (`exclusions.ini` `[ignore]`) | Implemented (`0.2.1`) | `exclusions.py`, `importer.py`, `cli.py` | `tests/test_exclusions.py`, `tests/test_import_failures.py` |
| Cross-platform per-vault & exclusions file locking | Implemented (`0.2.1`) | `locking.py`, `importer.py`, `exclusions.py` | `tests/test_import_locking.py`, `tests/test_exclusions.py` |
| Obsidian wikilink/embed rewriting + `madobsidian://<UUID>` | Implemented | `importer.py` | `tests/test_vault_import.py` |
| Strict INI configuration (`config.ini` + `config.example.ini`) | Implemented | `config.py` | `tests/test_config.py` |
| Watermarked WebP image previews (Pillow + bundled DejaVu Sans) | Implemented | `previews.py`, `assets/` | `tests/test_previews.py` |
| Full-length VFR-preserving H.264/AAC MP4 video previews | Implemented | `video_previews.py` | `tests/test_video_previews.py`, `tests/test_media_eligibility.py`, `tests/test_watermark_wrap.py` |
| Archive verification (`verify`) & disposable `index.json` rebuild | Implemented | `archive.py`, `cli.py` | `tests/test_archive.py` |
| Cross-platform filename safety validation | Implemented | `filenames.py` | `tests/test_importer.py`, `tests/test_archive.py` |
| CLI `status` subcommand | **Placeholder stub** | `cli.py` | `tests/test_cli.py` (tests static output only) |

---

## 2. Test Suite Inventory

The repository uses Python's standard `unittest` framework across **12 test modules** in `tests/`:

- `tests/test_archive.py` — Archive scanning, metadata/symlink/preview validation, `rebuild-index` atomicity.
- `tests/test_cli.py` — `--version`, `status`, `--help`, absence of obsolete `failed-imports`, legacy import & `--dry-run` rejection.
- `tests/test_config.py` — INI loading, validation, defaults, relative fonts, CLI `--config` forwarding.
- `tests/test_exclusions.py` — `exclusions.ini` format, validation, atomic/concurrent updates, pre-transaction failures, dry-run isolation.
- `tests/test_import_failures.py` — Per-file rollback, continuation, exclusions after failure, CLI exit codes & ignored output.
- `tests/test_import_locking.py` — Lock path stability and multi-process vault import serialization.
- `tests/test_importer.py` — Legacy copy-only importer, UUID collisions, symlinks, reserved filenames, partial batch behavior.
- `tests/test_media_eligibility.py` — Rejection of audio-only `.mp4` video candidates.
- `tests/test_previews.py` — Thresholds, resizing, transparency, animated GIF first-frame, EXIF orientation, Cyrillic watermark, bundled font/license.
- `tests/test_vault_import.py` — End-to-end vault import, Markdown rewriting, Unicode NFC/ambiguities, crash recovery, journal validation, config-change protection.
- `tests/test_video_previews.py` — Real FFmpeg/ffprobe integration tests (CFR, silent, VFR, delayed audio, rotation, failure cleanup; skips if FFmpeg/`libx264`/`ffprobe` unavailable).
- `tests/test_watermark_wrap.py` — Multiline Cyrillic wrapping across Pillow layout engines (RAQM/BASIC).

**Verification note (2026-11-25):** All **156 test methods** across the 12 modules pass after fixing the stale `ImportBatchResult()` mock in `tests/test_config.py`: `146` passed via `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest discover -s tests -v` (with `VideoPreviewTests` skipped when FFmpeg env vars are unset) and all `10` integration tests in `tests/test_video_previews.py` passed with `MADOBSIDIAN_FFMPEG` and `MADOBSIDIAN_FFPROBE` configured. Note that local `.venv` currently has a stale editable install pointing to `H:\madObsidian\src`, so `PYTHONPATH=src` is needed until `.venv` is reinstalled with `pip install -e .`.

---

## 3. Known Bugs, Discrepancies, and Immediate Issues

1. **CLI `status` is a static placeholder (`src/madobsidian/cli.py:44-46`):**
   - Prints `madObsidian 0.2.1: ready (no archive configured)` without inspecting any vault, config, or pending transactions.
2. **Dry-run summary count conflates recovery rows and planned imports (`src/madobsidian/importer.py:883-885`, `cli.py:55`):**
   - During `--dry-run`, pending recovery rows are appended to `batch`, inflating `"Would import N file(s)"`.
3. **Git & local environment hygiene:**
   - No Git tags exist for `0.1.0`, `0.2.0`, or `0.2.1`.
   - Release `0.2.1` was split across two commits (`53d1fd6` and `06e76c3`).
   - Local `.venv` editable install points to `H:\madObsidian\src` instead of `H:\Python\madObsidian\src` (requiring `PYTHONPATH=src` until `pip install -e .` is rerun).
   - Untracked local artifacts remain in the working tree (`pi-test-config.ini`, `test.py`; user-owned and must be preserved).

---

## 4. Technical Debt and Hardening Backlog

Captured from codebase inspection and the previous `README.md` TODO list:

- **Transaction journal & recovery hardening:** Strengthen semantic validation of `transaction.json` and further constrain recovery filesystem mutations against malformed local state.
- **Markdown parser edge cases (`importer.py:256-295`):**
  - YAML frontmatter is not excluded from link scanning.
  - Inline backtick scanning runs over the full note text rather than excluding fenced code block interiors first, creating potential backtick-pairing edge cases.
- **Cross-platform filename portability (`filenames.py`):** Currently blocks separators, `.`/`..`, Windows drive letters, and `metadata.json`, but does not yet handle Windows-invalid characters (`< > : " | ? *`), trailing dots/spaces, or Windows reserved device names (`CON`, `NUL`, `PRN`, etc.). Similarly, `exclusions.py:67` uses `Path(filename).name != filename`, which does not reject backslashes on Linux.
- **Unexpected non-UUID entries in `objects/` (`archive.py:314-317`):** Silently skipped by `scan_archive` instead of being reported as archive problems.
- **Decompression bomb / resource-exhaustion protection (`archive.py`, `previews.py`):** Pillow image decoding and verification do not enforce explicit pixel/memory ceilings beyond Pillow defaults.
- **Directory-level `fsync`:** File-level `os.fsync` is implemented for `metadata.json`, `transaction.json`, `index.json`, `exclusions.ini`, and `_atomic_text`, but parent directory `fsync` after renames is not yet implemented on POSIX.
- **Locking gaps outside `import_vault`:** `verify`, `rebuild-index`, and legacy `import_path` do not acquire `vault_import_lock`; running `verify` concurrently with `import_vault` can report transient `.incomplete-*` stages as problems.
- **Config font validation when watermark disabled (`config.py:172-183`):** Custom font paths are loaded via Pillow even if `[watermark] enabled = false`.
- **Syncthing rules verification:** End-to-end testing and hardening of `.stignore` behavior across devices.
- **Operator recovery workflow:** Clear operator documentation/tooling for persistent unrecoverable failures where neither `Incoming/` nor staging verifies automatically.
- **Comprehensive test-suite review:** Audit test isolation, coverage gaps, and consistency with documented behavior across all 12 test modules.

---

## 5. Deferred Work and Unresolved Questions

### Deferred (Not Approved for Implementation Unless Explicitly Reopened)

- **Plugin-owned pinning (`[pin]` semantics / cache manager):** Original-object retention policy on lightweight devices.
- **Obsidian plugin / `madobsidian://<UUID>` URI handler:** External consumer of the stable original links written into Markdown notes.

### Unresolved Design Questions

1. **Configurable `ffmpeg` / `ffprobe` paths in `config.ini` (planned for `0.2.2`):** Should optional tool paths live under `[videos]` (e.g., `ffmpeg_path = auto`, `ffprobe_path = auto`) or a dedicated `[tools]` section, and should environment variables (`MADOBSIDIAN_FFMPEG`/`MADOBSIDIAN_FFPROBE`) override `config.ini` or vice versa?
2. **Shared `exclusions.ini` across multiple vaults:** When multiple vaults pass the same `--config`, `exclusions.ini` is shared beside `config.ini`, so an ignored basename in Vault A is also ignored in Vault B. Is per-config sharing intentional, or should exclusions be vault-scoped?
3. **Ambiguous basename Unicode candidates:** Currently, if two NFC-equivalent files in `Incoming/` trigger an ambiguity on a basename-only link, the link is left unrewritten *while both files are still moved into `objects/`* (`test_vault_import.py:155-179`). Should this remain the intended behavior?
4. **Custom font validation when `watermark.enabled = false`:** Should `load_config` still fail on missing custom fonts when watermarking is disabled, or skip font loading?
5. **Future of `madObsidian status` and legacy `import_path`:** Should `status` inspect a vault/config (reporting pending transactions, ignored count, lock state), or remain minimal? Is legacy two-path `import_path` permanently supported or slated for deprecation?
