# Project Progress and Current State

The stable Phase 1 baseline is `0f9d25b` (`0.2.2`). Phase 2 development and its verification record belong to `develop`.

## Phase 2 verification

| Work | Exercised behavior |
| --- | --- |
| Transaction journal | Separate preparation, publication, completion and rollback phases. Verify media and preview integrity, validate targeted edits, retain conflicting evidence and write a verified rollback exclusion before journal removal. |
| Local coordination | Import, standalone recovery, verify and rebuild share a persistent per-vault lock. Separate Windows processes waited for the lock. |
| Incoming and exclusions | Obsidian setting precedence, absolute in-vault paths, malformed configuration, missing-directory logging, nested exclusions, manual retry, atomic competing writes, shared replica visibility and both NFC candidates retained. |
| Archive health | Unknown entries, pending transactions, transfer artifacts, malformed metadata, missing indexed objects, simulated memory exhaustion and configured scan limits preserve the previous index. |
| Portability and links | The staging move chooses the portable leaf. Explicit links recover through publication, including when the leaf differs from the original. The lexer covers wiki and inline Markdown forms, aliases, relative targets, code spans, fences, comments and frontmatter. |
| Status | Read-only diagnostics report local paths, tools, index readability, bounded journal count and exclusions. |
| Video | Real Windows encode, probe, decode and timeline checks used the source stream's numeric time base. Raspberry Pi FFmpeg 5.1.9 awaits manual validation after integration. |
| Syncthing | Two isolated Syncthing 2.1.7 processes on loopback synchronized a published journal after importer termination. Recovery on the second peer returned the corrected note and finalized object to the first. A mid-transfer stop left the receiver incomplete. Reconnection restored identical media bytes. Divergent offline exclusions produced a preserved conflict copy. |

### Process termination coverage

The matrix in `tests/test_transaction_crashes.py` uses `os._exit(83)`. It covers 36 scenarios, including repeated boundaries for portable names. Each case inspects media, notes or the index and runs recovery in fresh processes.

- 12 before publication cover input hash verification, stage creation, layout creation, journal temporary writes and replacements for moving, moved and prepared phases, source rename, preview completion and metadata write.
- 8 after publication cover directory rename, published and completed journals before and after replacement, Markdown temporary write and note replacement, and journal removal.
- 11 during rollback cover both rollback journal writes before and after replacement, verified source restoration, staged-original removal, exclusive-copy destination creation and verified copy completion, exclusion persistence, payload cleanup and journal deletion. Interruption with a partial restored file leaves the verified stage and journal available on both recovery attempts.
- 3 repeat source move, publication and partial note rewriting with a portable rename. Two index tests terminate immediately before and after atomic index replacement, then run recovery and rebuild twice.

These tests model abrupt process exit. Physical power failure, POSIX filesystem durability, physical multi-device networking and the actual Raspberry Pi remain outside the Windows verification record. A clean local scan cannot assert global Syncthing convergence. Independent writers must wait for synchronization before switching devices.

### Windows verification environment and commands

The final verbose run discovered 171 tests and finished in 321.488 seconds. It passed 170 with zero failures or errors. The single skip requires filenames that Windows cannot create. The branch-wide Git whitespace check passed. Python AST parsing covered all 28 source and test modules, and the documentation check resolved 31 local links across eight engineering documents.

Windows, Python 3.11.6, Pillow 13.0.0, FFmpeg and ffprobe from build `N-128915-g3d16b774d7-20261115`, and Syncthing 2.1.7. The project has no configured standalone type checker or linter. The checkout uses the existing virtual environment with the source tree on `PYTHONPATH`.

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:MADOBSIDIAN_FFMPEG = (Resolve-Path ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe).Path
$env:MADOBSIDIAN_FFPROBE = (Resolve-Path ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe).Path
$env:MADOBSIDIAN_TEST_SYNCTHING = 'C:\Programs\Syncthing\syncthing.exe'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git diff --check 0f9d25b..develop
```

Focused runs used `-m unittest discover -s tests -p <module>.py`. The archive, vault import, exclusions and process-lock modules passed 31, 45, 16 and 3 tests respectively. The crash module passed all four methods, including the 36 subprocess scenarios above. The standalone Syncthing transfer/reconnect test passed with real peers. The portability and diagnostics module passed six tests on Windows and skipped its POSIX-only filesystem case. Some tests inject exceptions, cap scan limits, substitute preview generation, or force the rollback hard-link fallback. Real FFmpeg integration covers CFR, VFR, delayed audio, rotation and threshold behavior.

The removed copy API had 19 dedicated tests in the baseline importer module. Its replacement tests exercise vault metadata, link isolation and UUID collisions. Existing image and video integration fixtures now submit referenced media to the vault importer. Counts across versions therefore represent different test inventories.

## 1. Phase 1 baseline inventory (historical)

| Capability | Status | Primary Modules | Verification / Tests |
| --- | --- | --- | --- |
| Vault move-only importer (`import_vault`, `--dry-run` with `batch.recoveries`) | Implemented (`0.2.2` dry-run separation) | `importer.py`, `cli.py` | `tests/test_vault_import.py` |
| Legacy copy-only importer (removed in Phase 2) | Implemented | `importer.py`, `cli.py` | `tests/test_importer.py` |
| Transactional journaling (`transaction.json`) & crash recovery | Implemented | `importer.py` | `tests/test_vault_import.py`, `tests/test_import_failures.py` |
| Per-file failure isolation, rollback & JSONL `import.log` | Implemented | `importer.py` | `tests/test_import_failures.py`, `tests/test_vault_import.py` |
| Persistent failed-import exclusions (the Phase 1 exclusions file `[ignore]`) | Implemented (`0.2.1`) | `exclusions.py`, `importer.py`, `cli.py` | `tests/test_exclusions.py`, `tests/test_import_failures.py` |
| Cross-platform per-vault & exclusions file locking | Implemented (`0.2.1`) | `locking.py`, `importer.py`, `exclusions.py` | `tests/test_import_locking.py`, `tests/test_exclusions.py` |
| Obsidian wikilink/embed rewriting + `madobsidian://<UUID>` link generation | Implemented *(URI opening plugin/handler deferred)* | `importer.py` | `tests/test_vault_import.py` |
| Strict INI configuration (`config.ini` + `config.example.ini`, `[tools]`, conditional font validation) | Implemented (`0.2.2`) | `config.py`, `video_previews.py`, `archive.py`, `cli.py` | `tests/test_config.py` |
| Watermarked WebP image previews (Pillow + bundled DejaVu Sans) | Implemented | `previews.py`, `assets/` | `tests/test_previews.py` |
| Full-length VFR-preserving H.264/AAC MP4 video previews | Implemented | `video_previews.py` | `tests/test_video_previews.py`, `tests/test_media_eligibility.py`, `tests/test_watermark_wrap.py` |
| Archive verification (`verify`) & disposable `index.json` rebuild | Implemented | `archive.py`, `cli.py` | `tests/test_archive.py` |
| Archive filename safety validation | Implemented, with portability gaps | `filenames.py` | `tests/test_importer.py`, `tests/test_archive.py` |
| CLI `status` subcommand | **Placeholder stub** | `cli.py` | `tests/test_cli.py` (tests static output only) |

---

## 2. Phase 1 test inventory (historical)

The repository uses Python's standard `unittest` framework across **12 test modules** in `tests/`:

- `tests/test_archive.py` — Archive scanning, metadata/symlink/preview validation, `rebuild-index` atomicity.
- `tests/test_cli.py` — `--version`, `status`, `--help`, absence of obsolete `failed-imports`, legacy import & `--dry-run` rejection.
- `tests/test_config.py` — INI loading, validation, defaults, relative fonts, conditional font validation (`watermark.enabled = false`), `[tools]` executable resolution precedence/lazy `auto`/CLI integration, CLI `--config` forwarding.
- `tests/test_exclusions.py` — the Phase 1 exclusions file format, validation, atomic/concurrent updates, pre-hashing & pre-transaction failure exclusions, dry-run isolation.
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

## 3. Issues at the Phase 1 baseline (historical)

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

## 4. Phase 1 hardening backlog (historical)

Captured from codebase inspection and the previous `README.md` TODO list:

- **Transaction journal & recovery hardening:** Strengthen semantic validation of `transaction.json` and further constrain recovery filesystem mutations against malformed local state.
- **Markdown parser edge cases (`importer.py:256-295`):**
  - YAML frontmatter is not excluded from link scanning.
  - Inline backtick scanning runs over the full note text rather than excluding fenced code block interiors first, creating potential backtick-pairing edge cases.
- **Cross-platform filename portability (`filenames.py`):** Currently blocks separators, `.`/`..`, Windows drive letters, and `metadata.json`, but does not yet handle Windows-invalid characters (`< > : " | ? *`), trailing dots/spaces, or Windows reserved device names (`CON`, `NUL`, `PRN`, etc.). Similarly, `exclusions.py:67` uses `Path(filename).name != filename`, which does not reject backslashes on Linux.
- **Unexpected non-UUID entries in `objects/` (`archive.py:314-317`):** Silently skipped by `scan_archive` instead of being reported as archive problems.
- **Decompression bomb / resource-exhaustion protection (`archive.py`, `previews.py`):** Pillow image decoding and verification do not enforce explicit pixel/memory ceilings beyond Pillow defaults.
- **Directory-level `fsync`:** File-level `os.fsync` is implemented for `metadata.json`, `transaction.json`, `index.json`, the Phase 1 exclusions file, and `_atomic_text`, but parent directory `fsync` after renames is not yet implemented on POSIX.
- **Locking gaps outside `import_vault`:** `verify`, `rebuild-index`, and the old copy importer do not acquire `vault_import_lock`; running `verify` concurrently with `import_vault` can report transient `.incomplete-*` stages as problems.
- **Syncthing rules verification:** End-to-end testing and hardening of `.stignore` behavior across devices.
- **Operator recovery workflow:** Clear operator documentation/tooling for persistent unrecoverable failures where neither `Incoming/` nor staging verifies automatically.
- **Comprehensive test-suite review:** Audit test isolation, coverage gaps, and consistency with documented behavior across all 12 test modules.

---

## 5. Deferred Work and Unresolved Questions

### Deferred (Not Approved for Implementation Unless Explicitly Reopened)

- **Plugin-owned pinning (`[pin]` semantics / cache manager):** Original-object retention policy on lightweight devices.
- **Obsidian plugin / registered `madobsidian://<UUID>` URI handler:** Generating `madobsidian://<UUID>` links in Markdown is already implemented; an Obsidian plugin or OS/app URI handler that opens and resolves those links is deferred.

Phase 2 resolves the former exclusion scope and status questions. Multi-vault orchestration remains deferred.
