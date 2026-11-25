# Project Progress and Current State

**Current version:** `0.2.2` (prepared release candidate on branch `master`; not yet committed or tagged).
**Last audited:** 2026-11-25 (definitive single full-suite run on Windows, Python `3.11.6`, `PYTHONPATH=src`, with `MADOBSIDIAN_FFMPEG` and `MADOBSIDIAN_FFPROBE` configured: **171/171 passed** in `235.108s`).

This document tracks what is implemented, what is verified by tests, known bugs and discrepancies, technical debt, deferred work, and open questions.

---

## Phase 2 transaction checkpoint

The subprocess crash suite covers source verification, stage and layout creation, temporary and replaced journals, source move, preview completion, metadata write, directory publication, Markdown replacement, finalization and journal removal. Rollback cases interrupt source restoration, staged-original unlink, cleanup and journal removal. Each case checks the surviving media and repeats recovery in a fresh process. The import does not write the disposable index. Physical power loss and Raspberry Pi durability remain unverified.

## 1. Implemented Functionality

| Capability | Status | Primary Modules | Verification / Tests |
| --- | --- | --- | --- |
| Vault move-only importer (`import_vault`, `--dry-run` with `batch.recoveries`) | Implemented (`0.2.2` dry-run separation) | `importer.py`, `cli.py` | `tests/test_vault_import.py` |
| Legacy copy-only importer (`import_path`) | Implemented | `importer.py`, `cli.py` | `tests/test_importer.py` |
| Transactional journaling (`transaction.json`) & crash recovery | Implemented | `importer.py` | `tests/test_vault_import.py`, `tests/test_import_failures.py` |
| Per-file failure isolation, rollback & JSONL `import.log` | Implemented | `importer.py` | `tests/test_import_failures.py`, `tests/test_vault_import.py` |
| Persistent failed-import exclusions (`exclusions.ini` `[ignore]`) | Implemented (`0.2.1`) | `exclusions.py`, `importer.py`, `cli.py` | `tests/test_exclusions.py`, `tests/test_import_failures.py` |
| Cross-platform per-vault & exclusions file locking | Implemented (`0.2.1`) | `locking.py`, `importer.py`, `exclusions.py` | `tests/test_import_locking.py`, `tests/test_exclusions.py` |
| Obsidian wikilink/embed rewriting + `madobsidian://<UUID>` link generation | Implemented *(URI opening plugin/handler deferred)* | `importer.py` | `tests/test_vault_import.py` |
| Strict INI configuration (`config.ini` + `config.example.ini`, `[tools]`, conditional font validation) | Implemented (`0.2.2`) | `config.py`, `video_previews.py`, `archive.py`, `cli.py` | `tests/test_config.py` |
| Watermarked WebP image previews (Pillow + bundled DejaVu Sans) | Implemented | `previews.py`, `assets/` | `tests/test_previews.py` |
| Full-length VFR-preserving H.264/AAC MP4 video previews | Implemented | `video_previews.py` | `tests/test_video_previews.py`, `tests/test_media_eligibility.py`, `tests/test_watermark_wrap.py` |
| Archive verification (`verify`) & disposable `index.json` rebuild | Implemented | `archive.py`, `cli.py` | `tests/test_archive.py` |
| Archive filename safety validation | Implemented, with portability gaps | `filenames.py` | `tests/test_importer.py`, `tests/test_archive.py` |
| CLI `status` subcommand | **Placeholder stub** | `cli.py` | `tests/test_cli.py` (tests static output only) |

---

## 2. Test Suite Inventory

The repository uses Python's standard `unittest` framework across **12 test modules** in `tests/`:

- `tests/test_archive.py` — Archive scanning, metadata/symlink/preview validation, `rebuild-index` atomicity.
- `tests/test_cli.py` — `--version`, `status`, `--help`, absence of obsolete `failed-imports`, legacy import & `--dry-run` rejection.
- `tests/test_config.py` — INI loading, validation, defaults, relative fonts, conditional font validation (`watermark.enabled = false`), `[tools]` executable resolution precedence/lazy `auto`/CLI integration, CLI `--config` forwarding.
- `tests/test_exclusions.py` — `exclusions.ini` format, validation, atomic/concurrent updates, pre-hashing & pre-transaction failure exclusions, dry-run isolation.
- `tests/test_import_failures.py` — Per-file rollback, continuation, exclusions after failure, CLI exit codes & ignored output.
- `tests/test_import_locking.py` — Lock path stability and multi-process vault import serialization.
- `tests/test_importer.py` — Legacy copy-only importer, UUID collisions, symlinks, reserved filenames, partial batch behavior.
- `tests/test_media_eligibility.py` — Rejection of audio-only `.mp4` video candidates.
- `tests/test_previews.py` — Thresholds, resizing, transparency, animated GIF first-frame, EXIF orientation, Cyrillic watermark, bundled font/license.
- `tests/test_vault_import.py` — End-to-end vault import, Markdown rewriting, Unicode NFC/ambiguities, crash recovery, journal validation, config-change protection.
- `tests/test_video_previews.py` — Real FFmpeg/ffprobe integration tests (CFR, silent, VFR, delayed audio, rotation, failure cleanup; skips if FFmpeg/`libx264`/`ffprobe` unavailable).
- `tests/test_watermark_wrap.py` — Multiline Cyrillic wrapping across Pillow layout engines (RAQM/BASIC).

### Definitive Test Verification Record (2026-11-25, `0.2.2` Release Candidate)

- **Executed command:**
  `$env:PYTHONPATH = 'src'; $env:MADOBSIDIAN_FFMPEG = 'h:\Python\madObsidian\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe'; $env:MADOBSIDIAN_FFPROBE = 'h:\Python\madObsidian\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe'; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`
- **Environment:** Windows (`Windows-10-10.0.19045-SP0`), Python `3.11.6`, Pillow `13.0.0`, `PYTHONPATH=src`, with `MADOBSIDIAN_FFMPEG` and `MADOBSIDIAN_FFPROBE` explicitly configured
- **Result:** `171` tests executed — **`171` passed, `0` failed, `0` errored, `0` skipped** in `235.108s` (`+15` new tests over the `0.2.1` baseline of `156`: `+1` in `test_exclusions.py`, `+3` in `test_vault_import.py`, `+11` in `test_config.py`)
- **Local environment note:** Local `.venv` currently has a stale editable install pointing to `H:\madObsidian\src`, so `PYTHONPATH=src` is required until `.venv` is reinstalled with `pip install -e .`.

**Platform behavior not yet verified by this run:**
- **Raspberry Pi / Linux FFmpeg 5.1.9 compatibility (`-enc_time_base:v filter`):** Real `ffmpeg` / `ffprobe` encoding (`libx264`, `aac`, `-bf 0`, `-enc_time_base:v filter`, VFR timeline probing) and performance on Raspberry Pi/Linux hardware remain unverified and unresolved.
- **POSIX OS primitives:** `fcntl.flock` cross-process locking (`src/madobsidian/locking.py`), POSIX `os.X_OK` executable permission checks on live Linux filesystems (exercised only via mocks on Windows), POSIX `O_NOFOLLOW` / symlink handling, and Linux filesystem case/separator behavior were not exercised on Windows.
- **Live multi-device Syncthing sync:** Real `.stignore` synchronization across Windows, Raspberry Pi, VPS, and Android has not been tested in this suite.

---

## 3. Known Bugs, Discrepancies, and Immediate Issues

1. **Raspberry Pi FFmpeg 5.1.9 `-enc_time_base:v filter` compatibility:** Separately tracked Linux/Pi compatibility issue; intentionally untouched in `0.2.2` and remains unverified on Raspberry Pi/Linux.
2. **NFC-equivalent basename ambiguity moves files while leaving basename-only links unchanged (`src/madobsidian/importer.py:878-882`):**
   - When multiple NFC-equivalent files in `Incoming/` make a basename-only Markdown reference ambiguous, current behavior moves both candidates into `objects/` without rewriting the note, which can leave links unresolved. Agreed Phase 2 correction: leave ambiguous candidates in `Incoming/` as skipped/ambiguous (see [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), Phase 2 #3).
3. **CLI `status` is a static placeholder (`src/madobsidian/cli.py:44-46`):**
   - Prints `madObsidian 0.2.2: ready (no archive configured)` without inspecting any vault, config, or pending transactions.
4. **Git & local environment hygiene:**
   - Historical releases (`0.1.0`, `0.2.0`, `0.2.1`) do not have Git tags; consistent annotated tagging (`vX.Y.Z` on the final release commit) begins with `v0.2.2` (no retroactive tag for `0.2.1`).
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
- **Syncthing rules verification:** End-to-end testing and hardening of `.stignore` behavior across devices.
- **Operator recovery workflow:** Clear operator documentation/tooling for persistent unrecoverable failures where neither `Incoming/` nor staging verifies automatically.
- **Comprehensive test-suite review:** Audit test isolation, coverage gaps, and consistency with documented behavior across all 12 test modules.

---

## 5. Deferred Work and Unresolved Questions

### Deferred (Not Approved for Implementation Unless Explicitly Reopened)

- **Plugin-owned pinning (`[pin]` semantics / cache manager):** Original-object retention policy on lightweight devices.
- **Obsidian plugin / registered `madobsidian://<UUID>` URI handler:** Generating `madobsidian://<UUID>` links in Markdown is already implemented; an Obsidian plugin or OS/app URI handler that opens and resolves those links is deferred.

### Unresolved Design Questions

1. **Shared `exclusions.ini` across multiple vaults:** When multiple vaults pass the same `--config`, `exclusions.ini` is shared beside `config.ini`, so an ignored basename in Vault A is also ignored in Vault B. Is per-config sharing intentional, or should exclusions be vault-scoped?
2. **Future of `madObsidian status` and legacy `import_path`:** Should `status` inspect a vault/config (reporting pending transactions, ignored count, lock state), or remain minimal? Is legacy two-path `import_path` permanently supported or slated for deprecation?
