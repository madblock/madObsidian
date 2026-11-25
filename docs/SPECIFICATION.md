# madObsidian Specification

This document is the authoritative technical and functional specification for **madObsidian** (current release: `0.2.1`). It describes both what the current implementation does and what the system is intended to do, explicitly marking discrepancies, unresolved questions, and future scope.

For architectural rationale, see [DECISIONS.md](DECISIONS.md). For current implementation status, known bugs, and technical debt, see [PROGRESS.md](PROGRESS.md). For quality gates, see [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md).

---

## 1. Purpose and Scope

### 1.1 Purpose

madObsidian is a local-first personal media archive and Obsidian vault integration tool. Its core responsibility is to ingest media files dropped into an Obsidian vault's `Incoming/` folder into content-verified, UUID-addressed object directories (`objects/<uuid>/`), generate lightweight watermarked previews for large images and videos, and rewrite vault Markdown references so that:

- Original files are preserved byte-for-byte with authoritative per-object metadata (`metadata.json`).
- Lightweight devices (e.g., Android phones or a VPS) can synchronize notes, metadata, small files (`shared/`), and previews (`previews/`) while excluding large originals (`originals/`) via Syncthing ignore rules.
- Notes retain a stable URI (`madobsidian://<UUID>`) pointing to the original object for future lookup or retrieval.

### 1.2 In-Repository Scope vs. External / Future Components

- **Implemented in this repository (verified in source code):**
  - Python CLI (`madObsidian` / `python -m madobsidian`) supporting `import` (vault mode and legacy copy-only mode), `verify`, `rebuild-index`, and a placeholder `status` command.
  - Transactional vault ingestion, crash recovery, Markdown wikilink rewriting, persistent failed-import exclusions (`exclusions.ini`), and cross-process locking.
  - Image (WebP) and full-length video (H.264/AAC MP4) preview generation with watermarks.
  - Archive integrity verification and disposable `index.json` reconstruction.
- **Outside this repository / future ecosystem (documented intent, not implemented here):**
  - An Obsidian plugin / URI handler resolving `madobsidian://<UUID>` links.
  - A cache manager with plugin-owned pinning (`[pin]` retention policy for original objects).
  - Automated Syncthing configuration on remote devices (madObsidian documents `.stignore` rules only).

---

## 2. System Architecture

### 2.1 Module Responsibilities

All application source lives under `src/madobsidian/` (Python `>= 3.11`, single Python runtime dependency `Pillow >= 10.0`):

| Module | Responsibility |
| --- | --- |
| `__init__.py` | Package version (`__version__ = "0.2.1"`). |
| `__main__.py` | Enables `python -m madobsidian`. |
| `cli.py` | Argument parsing (`argparse`), command dispatch, human-readable stdout/stderr reporting, dry-run unified diffs, exit codes. |
| `config.py` | Strict INI parsing and validation (`AppConfig`), built-in `DEFAULT_CONFIG`, safe vault-bounded directory resolution (`resolve_vault_directory`). |
| `filenames.py` | Cross-platform validation of archived original filenames (`filename_problem`). |
| `locking.py` | Cross-process persistent file locks (`process_lock`, `vault_lock_path`, `vault_import_lock`) via `msvcrt` (Windows) and `fcntl` (POSIX). |
| `exclusions.py` | Persistent failed-import exclusions in `exclusions.ini` (`load_exclusions`, `append_exclusion`, `exclusion_path`, `filename_key`). |
| `importer.py` | Vault import orchestration (`import_vault`), legacy copy-only importer (`import_path`), transactional staging (`_import_file`), Markdown planning/rewriting, crash recovery (`recover_transactions`), failure isolation and JSONL logging. |
| `previews.py` | Watermarked WebP image preview generation via Pillow (`generate_preview`, `REGISTERED_IMAGE_EXTENSIONS`). |
| `video_previews.py` | Full-length H.264/AAC watermarked MP4 preview generation and frame-level timeline/A-V sync verification via `ffmpeg`/`ffprobe` (`generate_video_preview`, `VIDEO_EXTENSIONS`). |
| `archive.py` | Read-only archive verification (`scan_archive`) and atomic disposable index reconstruction (`rebuild_index`). |
| `assets/` | Bundled `DejaVuSans.ttf` (DejaVu Fonts 2.37) and `DEJAVU-LICENSE.txt`. |

### 2.2 Deployment and Synchronization Architecture

**Target topology (documented in README and verified by cross-platform code):**

- **Full-archive hosts (Windows desktop, Raspberry Pi / Linux):** Run `madObsidian` and synchronize all vault files *except* unpublished staging directories.
- **Lightweight devices (VPS, Android):** Synchronize Markdown notes, `metadata.json`, `previews/`, and `shared/` files, while excluding `Incoming/` and `originals/`.
- **Required Syncthing `.stignore` on every synchronized device (including Windows and Raspberry Pi):**
  ```text
  objects/.incomplete-*
  ```
- **Additional `.stignore` rules on VPS and Android:**
  ```text
  Incoming
  !objects/*/metadata.json
  !objects/*/previews
  !objects/*/previews/**
  !objects/*/shared
  !objects/*/shared/**
  objects/*/*
  objects/.incomplete-*
  ```

**Local vs. synchronized files (verified in code):**

| File / Path | Location | Synchronized? | Purpose |
| --- | --- | --- | --- |
| Vault notes (`*.md`) | Inside vault | Yes | User notes; wikilinks rewritten after object publication. |
| `Incoming/` | Inside vault (configurable) | Full hosts only | Drop folder for new media. |
| `objects/<uuid>/` | Inside vault (configurable) | Partial or full per `.stignore` | Immutable archived media, metadata, and previews. |
| `objects/.incomplete-*` | Inside vault | **Never** (must be in `.stignore`) | Temporary staging directory during import. |
| `index.json` | Vault/archive root | Optional / rebuildable | Disposable index generated by `rebuild-index`. |
| `config.ini` | User-chosen local path | No (machine-local) | Optional processing configuration (`--config`). |
| `exclusions.ini` | Beside `config.ini`, or `vault.parent` | No (machine-local) | Persistent failed-import filename ignore list. |
| `exclusions.ini.lock` | Beside `exclusions.ini` | No | Cross-process update lock for `exclusions.ini`. |
| `.madobsidian-vault-<sha256>.lock` | `vault.parent` | No | Cross-process per-vault import serialization lock. |
| `import.log` | `vault.parent` | No | Append-only JSONL log of vault import errors. |

---

## 3. Vault and Archive Data Model

### 3.1 Vault Object Layout (`import_vault`)

Verified in `src/madobsidian/importer.py`:

```text
<objects_directory>/<uuid>/
├── metadata.json
├── shared/<original_filename>          # size_bytes < small_file_threshold_bytes
├── originals/<original_filename>       # size_bytes >= small_file_threshold_bytes
└── previews/                           # only when a preview was generated
    ├── preview.webp                    # eligible raster image
    └── preview.mp4                     # eligible video
```

Empty `shared/`, `originals/`, and `previews/` directories are omitted.

### 3.2 Legacy Copy-Only Object Layout (`import_path`)

Verified in `src/madobsidian/importer.py` and `src/madobsidian/archive.py`:

```text
<objects_directory>/<uuid>/
├── metadata.json
├── <original_filename>                 # stored directly in object root
└── previews/                           # only for eligible images/videos >= threshold
    ├── preview.webp
    └── preview.mp4
```

### 3.3 `metadata.json` Schema

Authoritative per-object metadata (verified in `importer.py:24-34, 72-85` and validated in `archive.py:51-124, 155-168`):

| Field | Type | Constraints |
| --- | --- | --- |
| `id` | `str` | Canonical lowercase UUIDv4 string matching the object directory name. |
| `original_filename` | `str` | Safe cross-platform basename (`filename_problem` passes; no separators, not `.` / `..`, no Windows drive prefix, not `metadata.json` case-insensitively). |
| `extension` | `str` | Exact `Path(original_filename).suffix` (empty string if none). |
| `mime_type` | `str` | Non-empty string from `mimetypes.guess_type(name)` or `"application/octet-stream"`. |
| `size_bytes` | `int` | Non-negative integer (booleans rejected) matching actual file size on disk. |
| `sha256` | `str` | 64-character hexadecimal SHA-256 digest matching original file contents. |
| `imported_at` | `str` | Canonical UTC ISO 8601 timestamp ending with `+00:00` (`datetime.now(timezone.utc).isoformat()`). |
| `original_path` | `str` | Relative POSIX path within object directory: `shared/<name>`, `originals/<name>`, or `<name>` (legacy). Optional only for pre-vault legacy objects verified by `archive.py`. |

### 3.4 `index.json` Schema

Disposable index written atomically to `<archive_root>/index.json` by `rebuild_index` (`archive.py:345-351`):

```json
{
  "version": 1,
  "objects": [
    {
      "metadata": { "...": "full metadata.json object" },
      "path": "objects/<uuid>/originals/<original_filename>"
    }
  ]
}
```

### 3.5 `transaction.json` Journal Schema

Written atomically via `_write_journal` (`importer.py:242-253`) and strictly validated by `_read_transaction` (`importer.py:435-483`):

| Field | Type | Constraints |
| --- | --- | --- |
| `id` | `str` | Canonical lowercase UUIDv4 string matching published directory (or any UUIDv4 inside `.incomplete-*`). |
| `source` | `str` | Vault-relative POSIX path directly under configured `incoming_directory`; no symlinks or traversal. |
| `original_path` | `str` | `shared/<filename>` or `originals/<filename>`, matching `size_bytes` against current `small_file_threshold_bytes`. |
| `sha256` | `str` | Lowercase 64-char hex SHA-256 of source file. |
| `size_bytes` | `int` | Non-negative integer file size. |
| `markdown` | `dict[str, list[dict]]` | Map of vault-relative `.md` POSIX paths to lists of `{"old": str, "new": str, "count": int}` (`count >= 1`). |
| `phase` | `str` | State machine value: `"moving"`, `"moved"`, or `"published"`. |
| `source_mode` | `str` | Must equal `"move"`. Legacy copy-mode journals without `"source_mode": "move"` are rejected. |

### 3.6 `import.log` Format

Append-only UTF-8 JSONL file at `vault.parent / "import.log"` (`importer.py:88-101`). Each line is a JSON object with keys `timestamp` (local timezone ISO 8601 seconds), `level` (`"ERROR"`), `source` (string path or `null` for vault-level errors), and `description` (whitespace-collapsed string truncated to 400 chars). Logging failures are silently swallowed so they never mask the underlying import error.

---

## 4. Configuration Specification

Verified in `src/madobsidian/config.py` and `config.example.ini`.

- When `--config` is omitted, built-in `DEFAULT_CONFIG` is used (`config_path = None`).
- Files are read as `utf-8-sig` with `ConfigParser(interpolation=None, strict=True)`.
- **Fail-closed validation:** Unknown sections, unknown keys, `DEFAULT` entries, out-of-range numbers, invalid booleans, unsupported formats/codecs, odd video `max_dimension`, and missing/unreadable custom font files fail before processing with `ConfigError` naming the config path, section, and key.
- **Directory constraints:** `incoming_directory` and `objects_directory` may be relative (resolved from vault root) or absolute, must resolve strictly inside the vault without traversing symlinks, and cannot overlap or nest inside each other (`importer.py:757-758`).
- **Font path resolution:** Non-`bundled` font paths expand `~`; relative paths resolve from `config_path.parent`, *not* the current working directory. Note (verified in `config.py:172-183`): custom font files are checked for existence and loaded via `ImageFont.truetype` at startup even if `watermark.enabled = false`.

| Section | Key | Default | Allowed Values / Bounds |
| --- | --- | --- | --- |
| `[general]` | `incoming_directory` | `Incoming` | Non-empty path without NUL; inside vault. |
| `[general]` | `objects_directory` | `objects` | Non-empty path without NUL; inside vault/archive root (cannot be root itself for `archive.py`). |
| `[general]` | `small_file_threshold_bytes` | `256000` | Integer `0` to `2^63 - 1`. |
| `[images]` | `max_dimension` | `960` | Integer `1` to `16384`. |
| `[images]` | `format` | `webp` | `webp` only. |
| `[images]` | `quality` | `85` | Integer `1` to `100` (lossy WebP; transparent images always use lossless WebP). |
| `[images]` | `method` | `6` | Integer `0` to `6`. |
| `[videos]` | `max_dimension` | `400` | Even integer `2` to `16384`. |
| `[videos]` | `format` | `mp4` | `mp4` only. |
| `[videos]` | `video_codec` | `libx264` | `libx264` only. |
| `[videos]` | `video_crf` | `25` | Integer `0` to `51`. |
| `[videos]` | `preset` | `medium` | `ultrafast`, `superfast`, `veryfast`, `faster`, `fast`, `medium`, `slow`, `slower`, `veryslow`. |
| `[videos]` | `audio_codec` | `aac` | `aac` only. |
| `[watermark]` | `enabled` | `true` | `true`/`false`, `yes`/`no`, `on`/`off`, `1`/`0`. |
| `[watermark]` | `font` | `bundled` | `bundled` or readable TrueType/OpenType font path. |
| `[watermark]` | `font_size` | `auto` | `auto` or integer `6` to `256`. |
| `[watermark]` | `opacity` | `auto` | `auto` or integer `0` to `255`. |
| `[watermark]` | `position` | `bottom-left` | `bottom-left`, `bottom-right`, `top-left`, `top-right`. |
| `[watermark]` | `margin` | `auto` | `auto` or integer `0` to `1024`. |

**Environment variables (verified in `video_previews.py:70-83`):**

- `MADOBSIDIAN_FFMPEG`: Path or `PATH`-resolvable executable name for `ffmpeg` (default: `ffmpeg`).
- `MADOBSIDIAN_FFPROBE`: Path or `PATH`-resolvable executable name for `ffprobe` (default: `ffprobe`).

---

## 5. CLI Specification

Verified in `src/madobsidian/cli.py`.

| Command | Mode / Behavior | Exit Codes |
| --- | --- | --- |
| `madObsidian` (bare) / `--help` | Prints argparse help. | `0` |
| `madObsidian --version` | Prints `madObsidian 0.2.1`. | `0` |
| `madObsidian status` | **Placeholder stub:** prints `madObsidian 0.2.1: ready (no archive configured)` without inspecting any vault or config. | `0` |
| `madObsidian import <vault> [--dry-run] [--config INI]` | Vault import mode (`import_vault`). Prints summary (`Imported` or `Would import` count, `skipped`, `ignored`, `failed`), per-file lines, recovery/ambiguity/Markdown info (plus unified diffs in `--dry-run`), `Skipped:`, `Ignored:`, and stderr `Failed:` / `Recovery Error:` lines. | `0` if `failures` is empty; `1` if any file failed or a fatal error occurred. |
| `madObsidian import <src> <archive> [--config INI]` | Legacy copy-only mode (`import_path`). Rejects `--dry-run` with exit `1`. Does not rewrite Markdown, use transactions, use exclusions, or acquire the vault lock. | `0` on full success; `1` on first error. |
| `madObsidian verify <root> [--config INI]` | Calls `scan_archive` without modifying files. Prints `Problem:` (stderr) and `Valid:` (stdout). Pass the vault/archive root containing `index.json`, not `objects/`. | `0` if no problems; `1` if any problem or error. |
| `madObsidian rebuild-index <root> [--config INI]` | Calls `rebuild_index`. Atomically replaces `<root>/index.json` only if `scan_archive` finds zero problems. | `0` if rebuilt; `1` if any problem or error (existing `index.json` untouched). |

**Observed CLI nuance (verified in `cli.py:55` and `importer.py:883-885`):** During `--dry-run`, pending `recovery_rows` are appended to `batch`, so the summary `"Would import N file(s)"` counts both pending recovery actions and newly planned imports. During real runs, `recovery_rows` are not appended to `batch`.

---

## 6. Vault Import Lifecycle

Verified in `src/madobsidian/importer.py` (`import_vault`, `_import_vault_locked`, `_import_file`, `recover_transactions`).

1. **Vault validation & locking:** Rejects symlink or non-directory vault roots. Acquires `vault_import_lock(root.resolve())` covering the entire run (including recovery, exclusion loading, scanning, staging, publication, and Markdown updates).
2. **Directory validation:** Resolves `incoming_directory` and `objects_directory` inside vault; rejects overlapping/nested directories or symlinked `Incoming`.
3. **Transaction recovery & cross-directory check:**
   - Runs `recover_transactions(root, dry_run=dry_run, config=config)` **before** loading `exclusions.ini` or discovering new candidates.
   - Runs `_reject_pending_transactions_outside(root, objects)` to abort if any `.incomplete-*` directory or `<uuid>/transaction.json` exists elsewhere in the vault (e.g., after an `objects_directory` config change).
   - Fatal recovery errors are logged to `import.log` (unless `dry_run`) and abort the run.
4. **Exclusion loading:** Resolves `exclusions.ini` via `exclusion_path(config.config_path, root)` and validates it via `load_exclusions`. Malformed `exclusions.ini` fails closed (logs to `import.log` unless `dry_run`, raises `ImportError`).
5. **Candidate discovery & filtering:**
   - Lists regular, non-symlink files directly under `incoming_directory` (`p.parent == incoming`), sorted case-insensitively, excluding sources already covered by `dry_run` recovery. Nested subdirectories under `Incoming/` are never imported.
   - Per direct file, checks in order:
     1. **Ignored (`exclusions.ini`):** Exact match on `path.name in excluded_filenames` → recorded in `batch.ignored` (`"listed in exclusions.ini"`) before hashing, previewing, or Markdown planning.
     2. **Hidden file:** `path.name.startswith(".")` → `batch.skipped` (`"hidden file"`).
     3. **Unsupported extension:** `_media_kind(path.name)` checks `REGISTERED_IMAGE_EXTENSIONS` (Pillow) then `VIDEO_EXTENSIONS` (24 extensions). Note: vault import does *not* use `mimetypes.guess_type()` fallback here. Unmatched files → `batch.skipped` (`"unsupported media extension"`).
     4. **Integrity inspection & filename check:** Reads `st_size` and `_sha256(path)`, then validates `filename_problem(path.name)`. Failures are isolated via `report_failure`.
6. **Markdown planning (`_markdown_plan`):**
   - For each remaining media candidate, allocates a proposed UUIDv4 and scans all non-symlink `*.md` files in the vault (sorted case-insensitively).
   - Computes active wikilink replacements and records any basename/Unicode ambiguities.
   - Files with zero planned Markdown edits *and* not part of any ambiguity are added to `batch.skipped` (`"no matching Markdown reference"`).
   - **Verified subtle behavior:** If two candidates in `Incoming/` collide under NFC normalization and trigger an ambiguity on a basename-only link, the link is left unchanged, *yet both candidates are still treated as referenced and imported* (`importer.py:878-882`, verified by `test_vault_import.py:155-179`).
7. **Dry-run branch:** If `dry_run=True`, appends recovery rows and planned candidate rows to `batch` and returns without modifying the vault, `exclusions.ini`, or `import.log`. (Acquiring `vault_import_lock` may create `.madobsidian-vault-<sha256>.lock` in `vault.parent`.)
8. **Per-file transactional import (`_import_file` + finalization):**
   - Creates staging directory `objects/.incomplete-<random>` via `tempfile.mkdtemp`.
   - Chooses destination subfolder: `shared/` if `size_bytes < small_file_threshold_bytes`, else `originals/`.
   - Writes `transaction.json` with `phase = "moving"`, `source_mode = "move"`, hash, size, and planned Markdown edits.
   - Atomically moves source via `source.rename(target)` (requires same filesystem; no copy fallback). If rename raises `OSError`, only removes the stage if source integrity is verified intact *and* target is absent; otherwise preserves stage + journal.
   - Verifies moved target size and SHA-256; updates `transaction.json` to `phase = "moved"`.
   - Generates preview inside `.incomplete-*/previews/` for files `>= threshold`: tries `generate_preview` (image) first; if `None`, runs `generate_video_preview` (video). Re-verifies moved original size and SHA-256 after preview processing.
   - Writes `metadata.json` (`open("x")`, `flush`, `os.fsync`).
   - Updates `transaction.json` to `phase = "published"`.
   - Publishes object via `temporary_dir.rename(objects / <uuid>)`.
   - Calls `recover_transactions(root, config=config, only_source=source_rel)` to replay the targeted Markdown edits atomically (`_atomic_text` with `os.fsync` + `os.replace`) and unlink `transaction.json`.

---

## 7. Markdown Reference Rewriting

Verified in `src/madobsidian/importer.py:239-399`:

- **Syntax handled:** Obsidian wikilinks and embeds matching `(?P<embed>!)?\[\[(?P<body>.*?)\]\]`, preserving optional `#heading` fragments and `|alias` text.
- **Not rewritten automatically:** Standard Markdown links (`[text](url)`), Obsidian block references (outside wikilink heading/alias parsing), and links targeting nested folders under `Incoming/`.
- **Code protection (`_protected_code_ranges`):** Skips wikilinks inside fenced code blocks (`` ``` `` or `~~~`, up to 3 leading spaces) and matching backtick inline code spans.
  - *Known limitation (tracked in TODO):* YAML frontmatter and certain inline-code/fenced-block backtick interactions are not fully isolated.
- **Target matching:** Normalizes paths with Unicode NFC (`/` separator).
  - Explicit paths with `/`: matched only when starting with `<incoming_directory>/`. Exact-name tie-breaker is used if NFC matches multiple candidates; unresolved ties are reported as ambiguous and left unchanged.
  - Basename-only targets (no `/`): rewritten only when matching *exactly one* eligible media candidate in the current `Incoming/` batch. Multiple matches are reported as ambiguous and left unchanged.
- **Replacement format (`_link_replacement`):**
  - Small files (`< threshold`): `<embed>[[<objects_dir>/<uuid>/shared/<filename><#heading><|alias>]]`.
  - Large files (`>= threshold`): `<embed>[[<objects_dir>/<uuid>/previews/<preview.webp|preview.mp4><#heading><|alias>]]` followed by a blank line and `[original](madobsidian://<uuid>)`.
  - Original note newline style (`\r\n` vs `\n`) is preserved.
- **Conflict-safe replay (`_apply_markdown_edits`):** Applies each counted `(old, new, count)` replacement only if `text.count(old) + text.count(new) == count`. Unrelated edits to the note are preserved; edits that alter the target reference itself raise a recovery conflict without overwriting the note.

---

## 8. Failure Isolation, Crash Recovery, and Persistent Exclusions

### 8.1 Per-File Failure Isolation and Rollback

Verified in `src/madobsidian/importer.py:781-814`:

- Each file failure is isolated in `import_vault`; remaining eligible files continue importing.
- A batch with any failure returns CLI exit code `1`.
- **If failure occurs after publication (`already_published=True`):** Marked `serious = True`, journal stays at `objects/<uuid>/transaction.json` for recovery, and **no exclusion is written**.
- **If failure occurs before publication:**
  - If `transaction_started=True` (the file was moved into staging), targeted recovery (`recover_transactions(..., only_source=relative_path)`) runs to restore the staged original to `Incoming/`, and `_source_matches(source, size, digest)` verifies both size and SHA-256 at `Incoming/`.
  - If failure occurs *before* a transaction starts and before hashing completes (`digest is None`, e.g., an unreadable file during `_sha256`), neither the file nor any Markdown note was modified; `_source_present(source, size)` confirms the source remains at `Incoming/`.
  - If rollback fails or the original cannot be verified at `Incoming/`, the failure is marked `serious = True`, staging/journal is retained, and **no exclusion is written**.
  - Otherwise, `append_exclusion(exclusions_file, source.name)` records the filename in `exclusions.ini` (`[ignore]`) so subsequent runs skip the failing file without repeating the error; manually removing the entry re-enables retry. (Note: `README.md` highlights large-file read failures as the motivating case; in `importer.py:796-841` this policy applies uniformly to all eligible media files.) If writing `exclusions.ini` itself fails, the failure reason notes both that the original was verified and that the exclusion update failed (without downgrading serious errors).

### 8.2 Crash Recovery (`recover_transactions`)

Verified in `src/madobsidian/importer.py:514-685`:

- **Pre-publication (`.incomplete-*` with `transaction.json`):** Validates journal fields and path safety (`_safe_relative` blocks symlinks, `..`, and vault escapes). Verifies `Incoming/` and staging hashes:
  - If `Incoming/` verifies, discards staging.
  - Else if staging verifies, restores to `Incoming/` via atomic `os.link` (fallback: exclusive `xb` copy + integrity check + unlink staging original) and removes staging.
  - If neither verifies, aborts and preserves staging for manual investigation.
- **Unjournaled `.incomplete-*` (`_cleanup_unjournaled_staging`):** Removes only if empty, or if a single staged file's SHA-256 matches an `Incoming/` file, or if no staged file exists while `Incoming/` has regular files. Otherwise preserves and aborts.
- **Post-publication (`objects/<uuid>/transaction.json`):** Requires `obj.name == data["id"]` and `phase == "published"`, checks original file hash and `metadata.json` consistency, replays Markdown edits via `_apply_markdown_edits`, writes notes atomically, and unlinks `transaction.json`.
- **Configuration stability requirement:** Pending transactions fail closed if `incoming_directory`, `objects_directory`, or `small_file_threshold_bytes` changed before recovery completes.

### 8.3 Persistent Exclusions (`exclusions.ini`)

Verified in `src/madobsidian/exclusions.py`:

- **Location:** `config_path.parent / "exclusions.ini"` when `--config` is used; `vault_root.parent / "exclusions.ini"` when unconfigured.
- **Format:** Single `[ignore]` section; keys are lowercase 64-char hex `SHA-256(filename.encode("utf-8"))`; values are JSON-encoded strings (`json.dumps(filename, ensure_ascii=False)`).
- **Validation (fail-closed):** Rejects symlinked `exclusions.ini`, `DEFAULT` entries, unknown sections, malformed keys, non-string/empty/path-like JSON values, and key/value hash mismatches.
- **Update atomicity:** Acquires `process_lock(exclusions.ini.lock)`, reloads file and byte snapshot, adds key if absent, writes UTF-8 LF temp file with `os.fsync`, checks symlink and compares live bytes against `snapshot` before `os.replace`.
- **Lifecycle:** Append-only from the importer's side; manually removing a line re-enables retry. Matching is by exact UTF-8 basename (not content hash and not NFC-folded). If multiple vaults share one `config.ini`, they share one `exclusions.ini`.

### 8.4 Concurrency Locking (`locking.py`)

Verified in `src/madobsidian/locking.py`:

- **Vault lock:** `vault_import_lock(vault)` locks `root.parent / f".madobsidian-vault-{sha256(normcase(str(root)))}.lock"`. Never unlinked after release. Covers `import_vault` end-to-end (including `--dry-run`).
- **Exclusions lock:** `process_lock(exclusions.ini.lock)` serializes `append_exclusion`.
- **Lock hierarchy:** Always `vault_import_lock` → `exclusions.ini.lock`, preventing deadlocks even when multiple vaults share a single `exclusions.ini`.
- **OS primitives:** Windows uses `msvcrt.locking(..., LK_NBLCK, 1)` on a guaranteed ≥1-byte file with 50ms retry polling on `EACCES`/`EDEADLK`/`EAGAIN` and `winerror` 33/36; POSIX uses blocking `fcntl.flock(..., LOCK_EX)`. OS releases handles automatically on process crash.
- **Not locked (current behavior):** `import_path` (legacy copy mode), `scan_archive` (`verify`), and `rebuild_index` do not acquire `vault_import_lock`.

---

## 9. Media Processing and Preview Generation

### 9.1 Image Previews (`previews.py`)

Verified in `src/madobsidian/previews.py`:

- Triggered when `size_bytes >= small_file_threshold_bytes`.
- Opens with Pillow (`Image.open`); if `UnidentifiedImageError` occurs on a `REGISTERED_IMAGE_EXTENSIONS` filename, raises `PreviewError` (aborting import); for non-image files returns `None`.
- Uses frame `0` for animated formats (e.g., GIF), applies `ImageOps.exif_transpose`, preserves alpha/transparency (`RGBA` → lossless WebP; opaque `RGB` → lossy WebP with configured `quality` and `method`).
- Scales with `thumbnail(..., LANCZOS)` so longest side `<= images.max_dimension` without enlarging.
- Watermark (when `watermark.enabled=true`): wraps `<filename> | <size> MB | <W> × <H>` (decimal MB `size / 1_000_000`), default opacity `150`, default font size `max(8, min(16, width // 55))` (auto-shrunk down to 6px if taller than image), default margin `max(4, min(12, width // 80))`.

### 9.2 Video Previews (`video_previews.py`)

Verified in `src/madobsidian/video_previews.py`:

- Candidate check (`is_video_candidate`): `size_bytes >= threshold` AND (`video/*` MIME type OR suffix in 24-extension `VIDEO_EXTENSIONS`).
- Probes streams and frame-by-frame presentation timestamps (`best_effort_timestamp`) via `ffprobe`; rejects videos without a video stream, invalid time base/dimensions, non-increasing PTS, or undeterminable duration. Accounts for rotation metadata (`side_data_list` / `rotate` tag).
- Scales to fit `videos.max_dimension` (default 400) without enlarging, padding to even canvas dimensions with black.
- Encodes via `ffmpeg` with `-copyts -start_at_zero`, `-fps_mode:v passthrough`, `-enc_time_base:v filter`, `-c:v libx264 -preset <preset> -bf 0 -crf <crf>`, `-c:a aac -b:a 128k` (first audio stream if present, optional via `0:a:0?`), `-movflags +faststart`, `format=yuv420p`.
- Watermark (when enabled): rendered to a temporary PNG and composited via `filter_complex` (`overlay=x=0:y=0:eof_action=repeat:shortest=1:repeatlast=1`). Format: `<filename>\n<size> MB | <W>×<H> | <HH:MM:SS.ss>[ | <fps> FPS avg]` (VFR only). Default opacity `155`, margin `6`, font size `max(9, min(14, width // 32))`.
- **Post-encode verification (`_check_output`):** Re-probes generated MP4 and enforces codec, dimensions, exact frame count, per-frame interval tolerance, video duration tolerance, relative A/V start offset tolerance, audio duration tolerance, and non-zero size before atomic `os.replace` into `previews/preview.mp4`.

---

## 10. Archive Verification and Index Recovery

Verified in `src/madobsidian/archive.py`:

- `scan_archive` validates archive root and `objects_directory` (rejecting symlinks and escapes), scans entries in `objects_directory`:
  - Reports `.incomplete-*` directories as problems.
  - Reports any `transaction.json` (or symlinked `transaction.json`) as a problem.
  - Reports non-canonical UUID directory names (e.g., uppercase or braced UUIDs).
  - **Known gap:** Silently skips non-UUID entries without `transaction.json` (tracked in [PROGRESS.md](PROGRESS.md)).
  - For each canonical UUID directory: opens `metadata.json` with `O_NOFOLLOW` where available, validates all schema fields, validates `original_path` layout against `small_file_threshold_bytes`, checks original file type/size/SHA-256 without following symlinks, validates expected WebP (decoded via Pillow `verify` + `load`) or H.264 MP4 preview (probed via `ffprobe` and null-decoded via `ffmpeg`), and rejects unexpected files in `previews/`.
- `rebuild_index` runs `scan_archive`; if *any* problem exists, leaves existing `index.json` unchanged. Otherwise writes `.index-incomplete-*.tmp` with `os.fsync` and atomically replaces `index.json`.

---

## 11. Summary of Known Discrepancies (Code vs. Intent)

| Area | Documented / Intended Behavior | Actual `0.2.1` Behavior | Reference |
| --- | --- | --- | --- |
| `madObsidian status` | Application/archive status command. | Static stub printing `ready (no archive configured)`. | `cli.py:44-46` |
| Dry-run summary count | Count of files that would be imported. | Includes pending `recovery_rows` in `len(rows)` during `--dry-run`. | `importer.py:883-885`, `cli.py:55` |
| Non-UUID dirs in `objects/` | Should be detected and reported. | Silently skipped during `scan_archive`. | `archive.py:314-317` |
| `tests/test_config.py` mock | Config CLI forwarding test should pass. | Statically observed broken mock (`return_value=[]` lacks `.skipped`/`.ignored`/`.failures`). | `tests/test_config.py:139` |
