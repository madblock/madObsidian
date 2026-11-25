# Project Progress and Current State

**Current version:** `0.2.1` (HEAD: `06e76c3`, branch `master`, synced with `origin/master`).
**Last audited:** 2026-11-24 (static code, test, and Git history audit; no tests executed during this documentation pass).

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
| Strict INI configuration (`config.ini` + `config.example.ini`) | Implemented | `config.py` | `tests/test_config.py` *(see Known Issue #1)* |
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
- `tests/test_config.py` — INI loading, validation, defaults, relative fonts, CLI `--config` forwarding *(contains Known Issue #1)*.
- `tests/test_exclusions.py` — `exclusions.ini` format, validation, atomic/concurrent updates, pre-transaction failures, dry-run isolation.
- `tests/test_import_failures.py` — Per-file rollback, continuation, exclusions after failure, CLI exit codes & ignored output.
- `tests/test_import_locking.py` — Lock path stability and multi-process vault import serialization.
- `tests/test_importer.py` — Legacy copy-only importer, UUID collisions, symlinks, reserved filenames, partial batch behavior.
- `tests/test_media_eligibility.py` — Rejection of audio-only `.mp4` video candidates.
- `tests/test_previews.py` — Thresholds, resizing, transparency, animated GIF first-frame, EXIF orientation, Cyrillic watermark, bundled font/license.
- `tests/test_vault_import.py` — End-to-end vault import, Markdown rewriting, Unicode NFC/ambiguities, crash recovery, journal validation, config-change protection.
- `tests/test_video_previews.py` — Real FFmpeg/ffprobe integration tests (CFR, silent, VFR, delayed audio, rotation, failure cleanup; skips if FFmpeg/`libx264`/`ffprobe` unavailable).
- `tests/test_watermark_wrap.py` — Multiline Cyrillic wrapping across Pillow layout engines (RAQM/BASIC).

**Verification note:** During the `0.2.1` release, only the 5 focused modules (`test_exclusions`, `test_import_locking`, `test_import_failures`, `test_vault_import`, `test_cli` — 73 tests) were executed. The full `unittest discover -s tests` suite has not been executed in this documentation-only pass.

---

## 3. Known Bugs, Discrepancies, and Immediate Issues

1. **Broken mock in `tests/test_config.py` (statically verified bug):**
   - `tests/test_config.py:139` (`test_vault_cli_config_option_is_forwarded`) patches `madobsidian.cli.import_vault` with `return_value=[]` (a plain `list`).
   - `src/madobsidian/cli.py:55-57` accesses `rows.skipped`, `rows.ignored`, and `rows.failures`, which exist on `ImportBatchResult` but not on `list` — causing an unhandled `AttributeError`.
   - Likely introduced in `27055dd` when `ImportBatchResult` was added, and missed because `test_config.py` was not in the focused 5-module test command.
2. **CLI `status` is a static placeholder (`src/madobsidian/cli.py:44-46`):**
   - Prints `madObsidian 0.2.1: ready (no archive configured)` without inspecting any vault, config, or pending transactions.
3. **Dry-run summary count conflates recovery rows and planned imports (`src/madobsidian/importer.py:883-885`, `cli.py:55`):**
   - During `--dry-run`, pending recovery rows are appended to `batch`, inflating `"Would import N file(s)"`.
4. **Git release hygiene:**
   - No Git tags exist for `0.1.0`, `0.2.0`, or `0.2.1`.
   - Release `0.2.1` was split across two commits (`53d1fd6` and `06e76c3`).
   - Untracked local artifacts remain in the working tree (`AGENTS.md`, `pi-test-config.ini`, `test.py`; note these are user-owned and must be preserved).

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

1. **Shared `exclusions.ini` across multiple vaults:** When multiple vaults pass the same `--config`, `exclusions.ini` is shared beside `config.ini`, so an ignored basename in Vault A is also ignored in Vault B. Is per-config sharing intentional, or should exclusions be vault-scoped?
2. **Ambiguous basename Unicode candidates:** Currently, if two NFC-equivalent files in `Incoming/` trigger an ambiguity on a basename-only link, the link is left unrewritten *while both files are still moved into `objects/`* (`test_vault_import.py:155-179`). Should this remain the intended behavior?
3. **Custom font validation when `watermark.enabled = false`:** Should `load_config` still fail on missing custom fonts when watermarking is disabled, or skip font loading?
4. **Tracking `AGENTS.md`:** Should `AGENTS.md` remain a local untracked file, or be committed to the repository?
5. **Future of `madObsidian status` and legacy `import_path`:** Should `status` inspect a vault/config (reporting pending transactions, ignored count, lock state), or remain minimal? Is legacy two-path `import_path` permanently supported or slated for deprecation?
