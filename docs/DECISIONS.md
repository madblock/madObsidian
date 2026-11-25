# Architectural Decisions (ADR)

This document records the key architectural decisions in **madObsidian**, their status, the evidence basis for each record, and their trade-offs. Detailed schemas and mechanics live in [SPECIFICATION.md](SPECIFICATION.md) rather than being repeated here.

**Evidence labels used below:**
- **Verified in code/tests** — directly observable in current source files and unit tests.
- **Established from Git history** — directly traceable to commits in `git log`.
- **Explicitly documented** — stated in `README.md` or local `AGENTS.md`.
- **Inferred rationale** — engineering motivation reasonably inferred from code structure and docs, where explicit design notes were not committed.

---

## ADR-001: UUID Object Directories with Authoritative Per-Object `metadata.json` and Disposable `index.json`

- **Status:** Accepted and implemented (`0.1.0`, commit `eac5049`, `154b26a`)
- **Evidence:** Verified in code/tests (`importer.py`, `archive.py`, `test_importer.py`, `test_archive.py`); explicitly documented in `README.md`.
- **Context:** Storing media by original filename risks collisions, broken links on rename, and single-file database corruption under multi-device file sync.
- **Decision:** Every imported file receives an immutable UUIDv4 directory `objects/<uuid>/` containing its own authoritative `metadata.json`. The top-level `index.json` is strictly a disposable cache rebuilt from per-object metadata via `rebuild-index`, and is never overwritten if any object fails verification.
- **Consequences:**
  - Sync-friendly: independent object directories avoid merge conflicts between devices.
  - Recoverable: deleting `index.json` loses zero authoritative state.
  - Trade-off: repeated imports of the same file create distinct UUID objects (no content deduplication).

---

## ADR-002: Size-Threshold Layout (`shared/` vs. `originals/`) and `madobsidian://<UUID>` Stable Links

- **Status:** Accepted and partially realized (`0.2.0`, commit `00269a7`; URI consumer plugin is future work)
- **Evidence:** Verified in code/tests (`importer.py`, `archive.py`, `test_vault_import.py`); explicitly documented in `README.md`.
- **Context:** Full-resolution photos and videos bloat storage and bandwidth on mobile devices (Android) and VPS hosts, while small files (< 256 KB by default) are cheap to sync everywhere without generating previews.
- **Decision:** Split vault object storage by `small_file_threshold_bytes` (default `256,000`):
  - Files `< threshold` go to `shared/<filename>` with no preview; Markdown links point directly to `shared/`.
  - Files `>= threshold` go to `originals/<filename>` with a preview in `previews/`; Markdown links point to the preview and append `[original](madobsidian://<UUID>)`.
  - Devices exclude `originals/` via Syncthing `.stignore` rules while retaining `metadata.json`, `previews/`, and `shared/`.
- **Consequences:**
  - Lightweight devices stay small without requiring a custom sync protocol.
  - Trade-off: `madobsidian://<UUID>` links currently act as durable identifiers only; opening originals through them awaits a future Obsidian plugin/handler. Changing `small_file_threshold_bytes` while pending transactions exist is blocked to preserve layout consistency.

---

## ADR-003: Move-Only Vault Imports via Same-Filesystem `Path.rename()` (Preserving Legacy Copy Mode)

- **Status:** Accepted and implemented (`0.2.0`, commits `00269a7`, `b68778d`)
- **Evidence:** Verified in code/tests (`importer.py:138-166`, `test_vault_import.py:485-520`); explicitly documented in `README.md`.
- **Context:** Copying large media files inside a synchronized vault doubles temporary disk usage and leaves ambiguity over when it is safe to delete the `Incoming/` source.
- **Decision:** Vault import (`import_vault`, one positional path) moves sources into staging using `Path.rename()` with no copy fallback, requiring `Incoming/` and `objects/` to reside on the same filesystem. The two-argument CLI form (`import_path`) is retained as a copy-only legacy importer for backward compatibility and rejects `--dry-run`.
- **Consequences:**
  - Atomic source handoff on the same filesystem with hash checks before move, after move, and after preview processing.
  - Trade-off: Cross-filesystem `Incoming/` and `objects/` layouts fail cleanly rather than falling back to copy; two distinct importer modes must be maintained and documented clearly.

---

## ADR-004: Referenced Media-Only Filtering in Vault Mode

- **Status:** Accepted and implemented (`0.2.0`, commit `7d3621b`)
- **Evidence:** Verified in code/tests (`importer.py:778-889`, `test_vault_import.py:82-179`); established from Git history.
- **Context:** `Incoming/` inside a Syncthing-synced vault may contain temporary sync files (`.syncthing.*.tmp`), hidden files, non-media files, or media dropped in before the user finishes referencing it in a note.
- **Decision:** Vault import considers only direct, non-symlink, non-hidden files under `Incoming/` whose extension is in Pillow's `REGISTERED_IMAGE_EXTENSIONS` or `VIDEO_EXTENSIONS`, and only imports files referenced by at least one active Markdown wikilink/embed (or involved in an ambiguous reference).
- **Consequences:**
  - Prevents premature ingestion of in-progress sync files or unlinked drops.
  - Trade-off: Unreferenced media stays in `Incoming/` silently until linked (reported as skipped); vault eligibility intentionally ignores MIME fallback (`mimetypes.guess_type`) used by legacy `is_video_candidate`.

---

## ADR-005: Counted Targeted Markdown Replacement Journals (`transaction.json`) Instead of Note Snapshots

- **Status:** Accepted and implemented (`0.2.0`, commit `00269a7`, refined in `27055dd`)
- **Evidence:** Verified in code/tests (`importer.py:309-399, 435-685`, `test_vault_import.py:251-372`); explicitly documented in `README.md`.
- **Context:** If an import crashes after publishing an object but before rewriting all referencing Markdown notes, the user may edit those notes before recovery runs. Saving full note snapshots would risk clobbering unrelated user edits on replay.
- **Decision:** Record targeted `(old, new, count)` replacement entries in `transaction.json`. During replay, verify `text.count(old) + text.count(new) == count` before replacing and writing via `_atomic_text` (`fsync` + `os.replace`). Stop with a recovery conflict if the references themselves changed.
- **Consequences:**
  - Idempotent, repeatable recovery that preserves unrelated user edits.
  - Trade-off: Manual edits to the exact reference being migrated trigger a conflict requiring human resolution; Markdown code-context detection still has known edge cases (frontmatter, nested inline code).

---

## ADR-006: Full-Length VFR-Preserving H.264/AAC Video Previews and WebP Image Previews with Bundled Font

- **Status:** Accepted and implemented (`0.1.0`, commits `6327e70`, `6e48ec4`; configurable in `0.2.0`, `00269a7`)
- **Evidence:** Verified in code/tests (`previews.py`, `video_previews.py`, `assets/`, `test_previews.py`, `test_video_previews.py`); explicitly documented in `README.md`.
- **Context:** Previews must render reliably in Obsidian on desktop and mobile, carry human-readable metadata watermarks including Cyrillic/Unicode filenames without depending on host OS fonts, and accurately represent variable-frame-rate (VFR) phone recordings.
- **Decision:**
  - Images (`>= threshold`): WebP (`<= 960px`, lossless when transparent, EXIF-oriented, first frame for animated formats).
  - Videos (`>= threshold`): Full-length H.264 MP4 (`<= 400px` even canvas, `-bf 0`, `-fps_mode:v passthrough`, `-enc_time_base:v filter`, AAC 128k audio, `+faststart`), strictly verified post-encode against frame counts, intervals, duration, and A/V start offset via `ffprobe`.
  - Bundle DejaVu Sans 2.37 (`DejaVuSans.ttf` + `DEJAVU-LICENSE.txt`) as the default watermark font.
- **Consequences:**
  - Portable watermarking across Windows and Raspberry Pi/Linux without system font configuration; strong protection against corrupted or desynced video previews.
  - Trade-off: Requires external `ffmpeg` (with `libx264` + `aac`) and `ffprobe` for video preview generation and video-containing archive verification; frame-by-frame probing adds processing time on low-power devices like Raspberry Pi.

---

## ADR-007: Fail-Closed Strict INI Configuration and Minimal Runtime Dependencies

- **Status:** Accepted and implemented (`0.2.0`, commit `00269a7`)
- **Evidence:** Verified in code/tests (`config.py`, `pyproject.toml`, `test_config.py`); explicitly documented in `README.md` and `AGENTS.md`.
- **Context:** Silent fallback on typos or unsupported codecs (e.g., `qualtiy = 80` or `format = png`) can produce unexpected archive artifacts that fail later verification. Deploying on Raspberry Pi also benefits from minimal Python dependencies.
- **Decision:** Use stdlib `configparser` (`strict=True`, `interpolation=None`) with explicit allowlists for every section, key, range, format, and codec; reject unknown or unsupported values at startup. Keep `Pillow >= 10.0` as the sole Python runtime dependency.
- **Consequences:**
  - Typos and unsupported settings fail immediately with actionable file/section/key diagnostics.
  - Zero extra dependency installation burden on Windows or Raspberry Pi.

---

## ADR-008: Persistent Failed-File Exclusions (`exclusions.ini` `[ignore]`) Replacing JSON Exclusions; `[pin]` Deferred

- **Status:** Partially accepted — `[ignore]` implemented (`0.2.1`, commit `53d1fd6`); `[pin]` remains deferred; JSON failed-file exclusions permanently rejected (`27055dd`, `test_cli.py`)
- **Evidence:** Verified in code/tests (`exclusions.py`, `importer.py`, `test_exclusions.py`, `test_cli.py:38-41`); established from Git history (`27055dd`, `53d1fd6`, stash names); explicitly documented in `README.md`.
- **Context:** An earlier JSON-based failed-file exclusion system with `failed-imports list/clear` CLI commands was removed (`27055dd`). Without exclusions, however, an unreadable or permanently broken file in `Incoming/` repeatedly fails on every scheduled import run. Conversely, excluding a file whose rollback failed or whose object was already published risks silent data loss or stranded transactions.
- **Decision:**
  - Never reintroduce JSON-based failed-file exclusions or `failed-imports` CLI subcommands.
  - Record safely preserved failed filenames in a human-inspectable `exclusions.ini` (`[ignore]` section, `SHA-256(utf-8 filename) = "<json-filename>"`) located beside `config.ini` (or `vault.parent`), loaded *after* transaction recovery, validated fail-closed, and updated atomically under a dedicated lock.
  - This covers both post-move failures after verified rollback (size + SHA-256 match) and pre-transaction read failures (where the file and Markdown links were never touched and `_source_present` confirms the source remains in `Incoming/`).
  - Require manual line removal to retry. Keep `[pin]` (cache manager retention policy) deferred.
- **Consequences:**
  - Stops repeated failure churn on unreadable or broken media while keeping exclusions transparent and outside the synchronized vault.
  - Trade-off: Filename-only keys mean replacing a broken file with a fixed file under the same name requires manual removal from `exclusions.ini`, and a transient read failure during hashing will also pause retries until manually removed.

---

## ADR-009: Cross-Process Persistent File Locks Outside the Synchronized Vault

- **Status:** Accepted and implemented (`0.2.1`, commit `53d1fd6`)
- **Evidence:** Verified in code/tests (`locking.py`, `exclusions.py`, `importer.py`, `test_import_locking.py`, `test_exclusions.py`); explicitly documented in `README.md`.
- **Context:** Automated runs (e.g., cron/scheduled tasks on Raspberry Pi or manual overlaps on Windows) could otherwise run two `import` processes against the same vault simultaneously, racing on moves and Markdown edits. Additionally, locking `exclusions.ini` directly on Windows would block `os.replace()`.
- **Decision:**
  - Serialize vault imports with a persistent lock file `vault.parent / .madobsidian-vault-<sha256(normcase(resolved_root))>.lock`.
  - Serialize `exclusions.ini` updates with a separate `exclusions.ini.lock` file.
  - Use OS-level locks (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX) and never unlink lock files after release.
  - Enforce a strict lock hierarchy: vault lock → exclusions lock.
- **Consequences:**
  - Deadlock-free, crash-safe (OS releases handles on exit), Windows `os.replace`-compatible, and isolated from Syncthing sync.
  - Trade-off: `--dry-run` may create the small coordination `.lock` file in `vault.parent`; legacy `import_path`, `verify`, and `rebuild-index` do not currently acquire the vault lock.

---

## ADR-010: Separation of Agent Operating Rules (`AGENTS.md`) from Engineering Documentation (`docs/`)

- **Status:** Accepted and adopted in documentation structure
- **Evidence:** `AGENTS.md` (tracked in repository, focused on enduring operational constraints) + documentation workflow.
- **Context:** Combining operational rules for AI coding agents (Git safety, approval gates, tool boundaries) with product specifications and design history makes both harder to maintain and risks stale product statements inside agent rules.
- **Decision:** Keep `AGENTS.md` focused strictly on durable operational rules and safety conventions for coding agents (how to work safely in the repo, including keeping docs synchronized with code/test changes), while keeping product behavior, design rationale, current state, quality gates, and roadmap in `docs/` and `CHANGELOG.md`.
- **Consequences:**
  - Product evolution updates `docs/` and `CHANGELOG.md` automatically alongside code/test changes without conflating specifications with agent guardrails.

---

## ADR-011: Independent Lazy/Explicit Executable Resolution for FFmpeg and ffprobe (`[tools]`)

- **Status:** Accepted and implemented (`0.2.2`)
- **Evidence:** Verified in code/tests (`config.py`, `video_previews.py`, `archive.py`, `cli.py`, `config.example.ini`, `tests/test_config.py`).
- **Context:** Users running portable FFmpeg builds on Windows or custom Linux layouts previously had to set `MADOBSIDIAN_FFMPEG` and `MADOBSIDIAN_FFPROBE` environment variables in every shell or scheduled task. At the same time, image-only vaults and unit-test environments without FFmpeg installed must not fail at configuration load time.
- **Decision:**
  - Add an optional `[tools]` section (`ffmpeg = auto`, `ffprobe = auto`) to `config.ini` and `config.example.ini`, preserving backward compatibility for configs omitting `[tools]`.
  - Resolve each tool independently with precedence: environment variable → explicit `[tools]` value → `PATH` (`auto`).
  - Keep `auto` lazy (no startup check in `load_config()`), while validating explicit values strictly without silent fallback: paths with path components resolve against `config_path.parent` (or CWD for env vars); bare names resolve strictly through `shutil.which()`; POSIX checks `os.X_OK`.
  - Thread `config.tools` consistently through `import`, `verify`, and `rebuild-index`.
- **Consequences:**
  - Machine-local `config.ini` can pin portable tool paths without breaking `config.example.ini` portability or image-only workflows.
  - Trade-off: Environment variables still override valid `[tools]` entries at runtime, while an invalid explicit `[tools]` value in `config.ini` fails fast during `load_config()` rather than being silently ignored.
