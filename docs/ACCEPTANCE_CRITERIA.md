# Acceptance Criteria

This document defines the concrete, verifiable invariants and quality gates that madObsidian must satisfy for every feature, bugfix, and release. New features should add feature-specific criteria to their plan while preserving all applicable invariants below.

For behavioral details, see [SPECIFICATION.md](SPECIFICATION.md). For workflow and review steps, see [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

---

## 1. Data Safety and Vault Integrity

- **AC-1.1 Byte-for-byte original preservation:** Every imported file's stored original must match the pre-import source byte-for-byte (verified by size and SHA-256). Originals are never modified in place.
- **AC-1.2 Atomic publication:** An object directory under `objects/<uuid>/` is published only after original verification, eligible preview generation, and `metadata.json` creation (`fsync`) succeed inside `.incomplete-*`. Failed imports must never leave a published partial object.
- **AC-1.3 No cross-filesystem silent copy in vault mode:** Vault imports move files via an atomic no-overwrite rename. If rename fails, the source remains intact in `Incoming/`, no object is published, and no Markdown note is modified.
- **AC-1.4 Strict vault boundary and symlink rejection:** Vault directories, `Incoming/`, `objects/`, source files, `metadata.json`, `transaction.json`, `madobsidian_exclusions.ini`, `index.json`, and journal-referenced paths must reject symlinks and path traversal (`..`, absolute escapes).
- **AC-1.5 Dry-run purity and result separation:** `--dry-run` must leave the vault, notes, `Incoming/`, `objects/`, `config.ini` and `madobsidian_exclusions.ini` unchanged. A missing Incoming emits a warning to the external `import.log`. Otherwise dry-run leaves the log unchanged. Creating the persistent coordination lock file `.madobsidian-vault-<sha256>.lock` in `vault.parent` is permitted. `ImportBatchResult` must report planned imports in the list (`len(batch)`) and pending recovery operations separately in `batch.recoveries`, and the CLI must report planned imports and pending recoveries separately.

- **AC-1.6 Syncthing conflicts.** Both import modes block on any visible conflict by default. Opting in to unrelated conflicts permits workspace, versioned workspace and ordinary note conflicts. Their contents stay untouched and cannot trigger an import. Conflicts affecting resolved Incoming, configured objects, index, exclusions or recovery artifacts still identify the blocking path before import. Existing unfinished-transfer checks apply in both modes.

---

## 2. Candidate Filtering and Markdown Rewriting

- **AC-2.1 Recursive Incoming scope:** Vault import considers only regular, non-symlink files under the resolved Incoming directory. Hidden files (`.` prefix), unsupported extensions, and unreferenced media must remain untouched in `Incoming/`.
- **AC-2.2 Code-context preservation:** Wikilinks inside fenced code blocks and inline code spans must not be rewritten.
- **AC-2.3 Link semantics and formatting preservation:**
  - Small files (`< threshold`) rewrite to `<objects_dir>/<uuid>/shared/<filename>`.
  - Large files (`>= threshold`) rewrite to `<objects_dir>/<uuid>/previews/preview.<webp|mp4>` and append `[original](madobsidian://<uuid>)`.
  - Aliases (`|alias`), heading fragments (`#heading`), and original note newline style (`\r\n` vs `\n`) are preserved.
- **AC-2.4 Ambiguity safety:** Ambiguous basename-only or Unicode-normalized references must be reported and left unchanged in Markdown.
- **AC-2.5 Conflict-safe atomic note updates:** Markdown rewrites are written atomically (`_atomic_text` with `fsync` + `os.replace`). Replay checks exact expected link counts and aborts with a conflict rather than overwriting concurrent edits to the target references, while preserving unrelated user edits.

---

## 3. Failure Isolation, Crash Recovery, and Exclusions

- **AC-3.1 Per-file isolation and continuation:** A failure on one file in a vault batch must not prevent other eligible files from importing.
- **AC-3.2 Nonzero failure exit code:** Any batch containing one or more file failures (or fatal errors) must return a nonzero CLI exit code (`1`), even when other files succeeded.
- **AC-3.3 Repeatable, idempotent crash recovery:**
  - Pre-publication interruptions restore the verified original to `Incoming/` (without overwriting an existing different file) and discard `.incomplete-*`.
  - Post-publication interruptions replay pending Markdown edits idempotently and unlink `transaction.json` without creating duplicate objects.
  - Configuration changes (`incoming_directory`, `objects_directory`, `small_file_threshold_bytes`) must fail closed while pending transactions exist.
  - Kill the importing or recovering process at each durable boundary. Inspect the surviving original and journal, then run recovery in two fresh processes and compare their filesystem results.
- **AC-3.4 Exclusion safety gates:**
  - Transaction recovery must always run *before* `madobsidian_exclusions.ini` is loaded or applied.
  - An exclusion must **never** be written when rollback failed, when an object was already published, or when the original cannot be confirmed intact at `Incoming/` (size and SHA-256 match after hashing/rollback; untouched file presence via `_source_present` when a read failure occurs before hashing starts so unreadable files and their Markdown links remain untouched and are ignored on subsequent runs).
  - Ordinary skips (hidden, unsupported, unreferenced) must never create exclusions.
  - Malformed `madobsidian_exclusions.ini` (bad syntax, bad hash key, hash/value mismatch, symlink) must fail closed before processing files.
  - Removing a line manually from `madobsidian_exclusions.ini` must re-enable retry on the next run.
  - Excluded files must be skipped before content hashing, preview generation, or Markdown planning, and reported separately from ordinary skips.
- **AC-3.5 No JSON failed-file exclusions:** Automatic JSON-based failed-file exclusions and obsolete `failed-imports list/clear` CLI commands must not be reintroduced.

---

## 4. Concurrency and Cross-Platform Compatibility

- **AC-4.1 Vault import serialization:** Concurrent `import_vault` processes targeting the same resolved vault must serialize via `vault.parent / .madobsidian-vault-<sha256>.lock` and never produce duplicate objects or corrupted notes.
- **AC-4.2 Atomic concurrent exclusion updates:** Concurrent writers to `madobsidian_exclusions.ini` must serialize via the local exclusions lock, reload under the lock, verify snapshot continuity, and replace atomically without losing entries or leaving `.exclusions-*.tmp` artifacts.
- **AC-4.3 Deadlock-free lock ordering:** Lock acquisition order must remain strictly `vault_import_lock` → the local exclusions lock; lock files persist after release and OS handles release automatically on crash.
- **AC-4.4 Windows and Raspberry Pi/Linux parity:** All features must work on Python `>= 3.11` across Windows (`msvcrt`) and Linux/Raspberry Pi (`fcntl`) with `Pillow >= 10.0` as the only Python runtime dependency (plus `ffmpeg`/`ffprobe` for video).

---

## 5. Media Processing and Archive Verification

- **AC-5.1 Image preview fidelity:** Eligible raster images (`>= threshold`) produce EXIF-oriented WebP previews within `images.max_dimension`, never enlarged, preserving transparency (lossless WebP) and Unicode watermarks. Corrupt recognized images must fail the import rather than silently skipping previews.
- **AC-5.2 Video preview fidelity:** Eligible videos (`>= threshold`) produce full-length H.264 MP4 previews (even dimensions within `videos.max_dimension`, first AAC audio stream when present, `+faststart`) verified post-encode for frame count, VFR intervals, duration, and relative A/V start offset. Any probe, encode, or verification failure aborts that file's import.
- **AC-5.3 Read-only `verify` and safe `rebuild-index`:** `verify` never mutates the archive. `rebuild-index` atomically replaces `index.json` only when zero archive problems exist; any invalid object, missing/undecodable preview, abandoned `.incomplete-*`, or unfinished `transaction.json` leaves the previous `index.json` intact and exits nonzero.

---

## 6. Configuration, Testing, and Documentation Gates

- **AC-6.1 Fail-closed configuration:** Unknown sections/keys, out-of-range values, odd video `max_dimension`, unsupported formats/codecs, invalid explicit `[tools]` executables, and unreadable fonts (when `watermark.enabled = true`) must fail at startup with `ConfigError` naming file, section, and key. When `watermark.enabled = false`, custom font existence/loading must be skipped. `[tools]` `auto` values must remain lazy during `load_config()` and fail clearly only when needed, honoring independent `env → [tools] → PATH` precedence with no silent fallback after an explicit value fails. Omitting `--config` or `[tools]` preserves built-in defaults.
- **AC-6.2 Test verification:** Every behavioral change adds or updates targeted `unittest` tests. Before release, the **full** test suite (`python -m unittest discover -s tests -v`) — not only a subset — must be evaluated (noting any skipped FFmpeg tests if FFmpeg is absent), and `git diff --check` must pass cleanly.
- **AC-6.3 Documentation sync:** Any change affecting CLI options, configuration, data layouts, recovery semantics, architectural decisions, or known issues must update the corresponding file(s) in `docs/`, `CHANGELOG.md`, and `README.md` (when entry-point usage changes) in the same change set.
