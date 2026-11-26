# madObsidian Specification

This document is the authoritative technical and functional specification for **madObsidian** (Phase 2 development version `0.3.0`). It describes both what the current implementation does and what the system is intended to do, explicitly marking discrepancies, unresolved questions, and future scope.

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
  - Python CLI (`madObsidian` / `python -m madobsidian`) supporting `import` (vault mode), `verify`, `rebuild-index`, and read-only `status` diagnostics.
  - Transactional vault ingestion, crash recovery, Markdown wikilink rewriting, persistent failed-import exclusions (`madobsidian_exclusions.ini`), and cross-process locking.
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
| `__init__.py` | Package version (`__version__ = "0.3.0"`). |
| `__main__.py` | Enables `python -m madobsidian`. |
| `cli.py` | Argument parsing (`argparse`), command dispatch, human-readable stdout/stderr reporting, dry-run unified diffs, exit codes. |
| `config.py` | Strict INI parsing and validation (`AppConfig`, `ToolsConfig`), built-in `DEFAULT_CONFIG`, explicit executable resolution (`resolve_explicit_executable`), safe vault-bounded directory resolution (`resolve_vault_directory`). |
| `filenames.py` | Cross-platform validation of archived original filenames (`filename_problem`). |
| `locking.py` | Cross-process persistent file locks (`process_lock`, `vault_lock_path`, `vault_import_lock`) via `msvcrt` (Windows) and `fcntl` (POSIX). |
| `exclusions.py` | Persistent failed-import exclusions in `madobsidian_exclusions.ini` (`load_exclusions`, `append_exclusion`, `exclusion_path`, `filename_key`). |
| `importer.py` | Vault import orchestration (`import_vault`), transactional staging (`_import_file`), Markdown planning/rewriting, crash recovery (`recover_transactions`), failure isolation and JSONL logging. |
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
| `madobsidian_exclusions.ini` | Vault root | Yes | Incoming-relative exclusions shared between devices. |
| `.madobsidian-vault-<sha256>.exclusions.lock` | Vault parent | No | Cross-process update lock for `madobsidian_exclusions.ini`. |
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

### 3.2 Import interface

The vault importer is the sole write path for creating archive objects. Verification can inspect existing per-object metadata.

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
| `source` | `str` | Vault-relative POSIX path under the resolved `incoming_directory`; no symlinks or traversal. |
| `original_path` | `str` | `shared/<filename>` or `originals/<filename>`, matching `size_bytes` against current `small_file_threshold_bytes`. |
| `sha256` | `str` | Lowercase 64-char hex SHA-256 of source file. |
| `size_bytes` | `int` | Non-negative integer file size. |
| `markdown` | `dict[str, list[dict]]` | Map of vault-relative `.md` POSIX paths to lists of `{"old": str, "new": str, "count": int}` (`count >= 1`). |
| `phase` | `str` | `moving`, `moved`, `prepared` (metadata complete), `published`, `completed`, `rolling_back`, or `rolled_back`. Publication occurs when the staging directory is renamed to its UUID. A `prepared` journal in a UUID directory rolls forward. |
| `preview` | optional object | Generated preview path, size and SHA-256, checked before published-object recovery. |
| `failure` | optional string | Reason retained through rollback and exclusion recording. |
| `source_mode` | `str` | Must equal `"move"`. Legacy copy-mode journals without `"source_mode": "move"` are rejected. |

### 3.6 `import.log` Format

Append-only UTF-8 JSONL file at `vault.parent / "import.log"` (`importer.py:88-101`). Each line is a JSON object with keys `timestamp` (local timezone ISO 8601 seconds), `level` (`"ERROR"` or `"WARNING"`), `source` (string path or `null` for vault-level errors), and `description` (whitespace-collapsed string truncated to 400 chars). Logging failures are silently swallowed so they never mask the underlying import error.

---

## 4. Configuration Specification

Verified in `src/madobsidian/config.py` and `config.example.ini`.

- When `--config` is omitted, built-in `DEFAULT_CONFIG` is used (`config_path = None`).
- Files are read as `utf-8-sig` with `ConfigParser(interpolation=None, strict=True)`.
- **Fail-closed validation:** Unknown sections, unknown keys, `DEFAULT` entries, out-of-range numbers, invalid booleans, unsupported formats/codecs, odd video `max_dimension`, and missing/unreadable custom font files fail before processing with `ConfigError` naming the config path, section, and key.
- **Directory constraints:** `incoming_directory` and `objects_directory` may be relative (resolved from vault root) or absolute, must resolve strictly inside the vault without traversing symlinks, and cannot overlap or nest inside each other (`importer.py:757-758`).
- **Font path resolution:** Non-`bundled` font paths expand `~`; relative paths resolve from `config_path.parent`, *not* the current working directory. When `watermark.enabled = true`, custom font files are strictly validated for existence and loadability via `ImageFont.truetype` at startup; when `watermark.enabled = false`, custom font existence and Pillow loading are skipped (`config.py:227-244`).

| Section | Key | Default | Allowed Values / Bounds |
| --- | --- | --- | --- |
| `[general]` | `incoming_directory` | `Incoming` | Non-empty path without NUL; inside vault. |
| `[general]` | `objects_directory` | `objects` | Non-empty path without NUL; inside vault/archive root (cannot be root itself for `archive.py`). |
| `[general]` | `small_file_threshold_bytes` | `256000` | Integer `0` to `2^63 - 1`. |
| `[general]` | `allow_unrelated_syncthing_conflicts` | `false` | Boolean. Allows import and dry-run with conflict copies outside managed data. |
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
| `[tools]` | `ffmpeg` | `auto` | `auto` (lazy `PATH` lookup) or explicit executable path / bare executable name. |
| `[tools]` | `ffprobe` | `auto` | `auto` (lazy `PATH` lookup) or explicit executable path / bare executable name. |

**External tool resolution (`config.py:resolve_explicit_executable`, `video_previews.py:_executable`, `archive.py`, `cli.py`):**

- `ffmpeg` and `ffprobe` are resolved **independently**, using strict precedence:
  1. Environment variable: `MADOBSIDIAN_FFMPEG` / `MADOBSIDIAN_FFPROBE` (non-empty value).
  2. Explicit value in `[tools]` (`ffmpeg` / `ffprobe`). Omitting `[tools]` defaults both to `auto`.
  3. Automatic `PATH` discovery via `shutil.which()`.
- **Lazy `auto` discovery:** When `[tools]` is `auto` (or omitted), `load_config()` does not require `ffmpeg` or `ffprobe` to exist; discovery occurs only when a video preview or video archive verification actually runs, failing with an actionable `VideoPreviewError` if unavailable.
- **Fail-closed explicit validation (no silent fallback):**
  - Relative paths containing path components resolve relative to `config_path.parent` (for `[tools]`) or the current working directory (for environment variables); absolute paths and `~` expansion are supported.
  - Bare executable names without path components (e.g., `my-ffmpeg`) resolve strictly through `shutil.which()` and are **not** interpreted relative to `config_path.parent`.
  - On POSIX, resolved files must exist and have execute permission (`os.X_OK`). On Windows, existing files and `PATHEXT` executable lookups via `shutil.which()` are supported.
  - Invalid explicit values in `[tools]` fail at `load_config()` with `ConfigError`; invalid environment variables fail at execution with `VideoPreviewError` without falling back to `[tools]` or `PATH`.
- **Consistent CLI integration:** `import`, `verify`, and `rebuild-index` all thread `config.tools` through the same `_executable()` resolver.

---

## 5. CLI Specification

Verified in `src/madobsidian/cli.py`.

| Command | Mode / Behavior | Exit Codes |
| --- | --- | --- |
| `madObsidian` (bare) / `--help` | Prints argparse help. | `0` |
| `madObsidian --version` | Prints `madObsidian 0.3.0`. | `0` |
| `madObsidian status [vault] [--config INI]` | Reports configuration, tools, directories, index readability, pending journals and exclusions. Examines up to 1,000 object entries and 1 MiB of index data. | `0` on successful inspection, `1` for diagnostic errors. |

| `madObsidian import <vault> [--dry-run] [--config INI]` | Vault import mode (`import_vault`). Prints summary (`Imported N file(s); skipped ...; ignored ...; failed ...` for real runs; `Would import N file(s); pending recoveries M; skipped ...; ignored ...; failed ...` for `--dry-run`), `Pending recovery:` lines, per-file planned/imported lines (plus unified diffs in `--dry-run`), `Skipped:`, `Ignored:`, and stderr `Failed:` / `Recovery Error:` lines. | `0` if `failures` is empty; `1` if any file failed or a fatal error occurred. |
| `madObsidian verify <root> [--config INI]` | Calls `scan_archive` (forwarding `config.tools`) without modifying files. Prints `Problem:` (stderr) and `Valid:` (stdout). Pass the vault/archive root containing `index.json`, not `objects/`. | `0` if no problems; `1` if any problem or error. |
| `madObsidian rebuild-index <root> [--config INI]` | Calls `rebuild_index` (forwarding `config.tools`). Atomically replaces `<root>/index.json` only if `scan_archive` finds zero problems. | `0` if rebuilt; `1` if any problem or error (existing `index.json` untouched). |

---

## 6. Vault import lifecycle

The public importer, recovery, verify and index rebuild acquire the same local vault lock. Recovery processes pending transactions before candidate discovery. Obsidian's `.obsidian/app.json` supplies `attachmentFolderPath`. A missing property uses the INI fallback. Malformed settings fail explicitly. Absolute and relative directories must resolve inside the vault. A missing Incoming is empty and produces an external `import.log` warning in real and dry-run modes.

Discovery walks nested Incoming paths. Hidden, excluded and unsupported files stay in place. Only referenced media proceeds to import. Basename-only NFC ambiguity leaves all matching candidates in Incoming with reason `nfc_ambiguity`. Resolve the reference and remove the corresponding entries manually.

The journal records the source path, SHA-256, size, archive-relative original, UUID and counted note edits before the source moves. The importer verifies the staged bytes, generates eligible previews, writes metadata and records `prepared`. Renaming the stage to its UUID publishes the object. Finalization validates the metadata and source integrity, applies note edits and records `completed` before journal removal. Import leaves index rebuilding to its own command.

Every Incoming path component participates in portability checks. Unsafe components and case- or normalization-equivalent collisions select a sanitized archive leaf during the same move. The suffix begins with six Base32 characters of SHA-256 over the original Incoming-relative POSIX path and grows until unique. The journal retains the original source path for rollback. Windows uses its no-overwrite rename semantics. Linux uses `renameat2(RENAME_NOREPLACE)` and fails explicitly when unavailable. Renamed objects also record their original vault-relative `source_path` in metadata.

## 7. Markdown references

The lexer in `markdown.py` recognizes single-line wikilinks and embeds with headings and aliases, and inline Markdown links and images with simple labels, percent-encoded or angle-bracket destinations and optional double-quoted titles. Markdown destinations are relative to the note. Wikilinks accept unique basenames, vault-qualified paths and explicit note-relative paths. Large media links retain the original UUID link. Counted journal edits preserve line endings, unrelated content and inactive examples.

Fenced code, four-space or tab-indented lines, matched backtick spans, HTML comments and leading YAML frontmatter are inactive. Fence detection recognizes blockquote prefixes and list markers. Reference definitions, nested Markdown labels, bare destinations with balanced parentheses, HTML attributes, and full CommonMark container parsing are outside the grammar. Use angle brackets or URL encoding for complex destinations.

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
  - Otherwise, `append_exclusion(exclusions_file, relative_path, message=reason)` records the filename in `madobsidian_exclusions.ini` (`[ignore]`) so subsequent runs skip the failing file without repeating the error; manually removing the entry re-enables retry. (Note: `README.md` highlights large-file read failures as the motivating case; in `importer.py:796-841` this policy applies uniformly to all eligible media files.) If writing `madobsidian_exclusions.ini` itself fails, the failure reason notes both that the original was verified and that the exclusion update failed (without downgrading serious errors).

### 8.2 Crash Recovery (`recover_transactions`)

Verified in `src/madobsidian/importer.py:514-685`:

- **Pre-publication (`.incomplete-*` with `transaction.json`):** Validates journal fields and path safety (`_safe_relative` blocks symlinks, `..`, and vault escapes). Verifies `Incoming/` and staging hashes:
  - If `Incoming/` verifies, discards staging.
  - Else if staging verifies, restores to `Incoming/` via atomic `os.link` (fallback: exclusive `xb` copy + integrity check + unlink staging original) and removes staging.
  - If neither verifies, aborts and preserves staging for manual investigation.
- **Unjournaled `.incomplete-*` (`_cleanup_unjournaled_staging`):** Removes only if empty, or if a single staged file's SHA-256 matches an `Incoming/` file, or if no staged file exists while `Incoming/` has regular files. Otherwise preserves and aborts.
- **Post-publication (`objects/<uuid>/transaction.json`):** Requires `obj.name == data["id"]` and `phase in {"prepared", "published", "completed"}`, checks original file hash and `metadata.json` consistency, replays Markdown edits via `_apply_markdown_edits`, writes notes atomically, and unlinks `transaction.json`.
- **Recovery outcomes:** A completed replay reports `completed`. A verified rollback records its exclusion before removing the journal and reports `rolled_back`. Standalone recovery distinguishes missing prerequisites or I/O failures from contradictory journal evidence via `IncompleteRecovery` and `UnverifiableRecovery`. Preserve the journal, source and archive bytes when resolving a conflict manually.
- **Configuration stability requirement:** Pending transactions fail closed if `incoming_directory`, `objects_directory`, or `small_file_threshold_bytes` changed before recovery completes.

### 8.3 Shared exclusions

`<vault>/madobsidian_exclusions.ini` contains an `[ignore]` section. The lowercase SHA-256 key hashes the UTF-8 Incoming-relative path with canonical slash separators. Exact Unicode spelling distinguishes NFC-equivalent files. Each JSON value carries `path`, `reason` and `message`. Other INI sections and comments are retained. The application locks locally, reloads and compares the exact bytes before an atomic replace. Removing the line authorizes another attempt. Visible Syncthing conflict copies require operator attention.

### 8.4 Coordination

The vault lock and exclusion-update lock live in the vault parent, with the latter acquired inside the former during import. Standalone recovery and both archive commands share import's vault lock. Independent devices must serialize imports operationally and wait for synchronization before switching writers.

Normal and dry-run imports scan for Syncthing artifacts before recovery or import work. With the default setting, every visible conflict copy blocks. With `allow_unrelated_syncthing_conflicts = true`, the following conflicts still block:

- The resolved Incoming tree (including an Obsidian `attachmentFolderPath` override) and the configured objects tree, including media, metadata, journals and stages. A conflict copy of a directory on the path to either tree also blocks.
- The vault-root `index.json` and `madobsidian_exclusions.ini` and their `.index-incomplete-*.tmp` and `.exclusions-*.tmp` write files.
- Recovery artifacts outside the current objects tree, including `.incomplete-*` staging directories, UUID object metadata and transaction journals, and `*.md.madobsidian-tmp` files beside notes.

Unrelated conflict copies may remain anywhere else, including in `.obsidian` or `.stversions`. A conflict note or a note inside a conflict directory cannot supply an import reference. Recovery refuses a journal that explicitly names such a note. An unfinished Syncthing transfer still blocks outside Incoming, including a transfer of a conflict copy. Ordinary partial transfers inside Incoming retain their existing handling. The option applies only to import and dry-run. Standalone recovery and archive verification retain their existing checks.

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
- The streaming timeline scan counts named FFprobe frame records. Each decoded frame must supply an integer timestamp in stream ticks. Nested side data and its line separators do not contribute frames or intervals.
- Encodes via `ffmpeg` with `-copyts -start_at_zero`, `-fps_mode:v passthrough`, `-enc_time_base:v NUM:DEN`, `-c:v libx264 -preset <preset> -bf 0 -crf <crf>`, `-c:a aac -b:a 128k` (first audio stream if present, optional via `0:a:0?`), `-movflags +faststart`, `format=yuv420p`.
- Watermark (when enabled): rendered to a temporary PNG and composited via `filter_complex` (`overlay=x=0:y=0:eof_action=repeat:shortest=1:repeatlast=1`). Format: `<filename>\n<size> MB | <W>×<H> | <HH:MM:SS.ss>[ | <fps> FPS avg]` (VFR only). Default opacity `155`, margin `6`, font size `max(9, min(14, width // 32))`.
- **Post-encode verification (`_check_output`):** Re-probes generated MP4 and enforces codec, dimensions, exact frame count, per-frame interval tolerance, video duration tolerance, relative A/V start offset tolerance, audio duration tolerance, and non-zero size before atomic `os.replace` into `previews/preview.mp4`.
- When the source audio sample rate is available, the MP4 muxer uses that rate as its movie time scale. This gives edit-list start offsets sample precision when encoding delayed AAC at that rate.

---

## 10. Archive Verification and Index Recovery

Verified in `src/madobsidian/archive.py`:

- `scan_archive` validates archive root and `objects_directory` (rejecting symlinks and escapes), scans entries in `objects_directory`:
  - Reports `.incomplete-*` directories as problems.
  - Reports any `transaction.json` (or symlinked `transaction.json`) as a problem.
  - Reports non-canonical UUID directory names (e.g., uppercase or braced UUIDs).
  - Unexpected entries, visible sync artifacts, unavailable previously indexed IDs, decoder errors and resource limits produce an incomplete local verification report. Limits include 100,000 archive entries, 1 MiB per metadata file, 64 MiB cumulative metadata and 1,024 entries per object subdirectory.
  - For each canonical UUID directory: opens `metadata.json` with `O_NOFOLLOW` where available, validates all schema fields, validates `original_path` layout against `small_file_threshold_bytes`, checks original file type/size/SHA-256 without following symlinks, validates expected WebP (decoded via Pillow `verify` + `load`) or H.264 MP4 preview (probed via `ffprobe` and null-decoded via `ffmpeg`), and rejects unexpected files in `previews/`.
- `rebuild_index` runs `scan_archive`; if *any* problem exists, leaves existing `index.json` unchanged. Otherwise writes `.index-incomplete-*.tmp` with `os.fsync` and atomically replaces `index.json`.

---

The local verifier cannot observe changes still queued on another device. Concurrent imports on independent machines are unsupported.
