# Implementation Plan and Development Workflow

This document serves two purposes:
1. **Part 1** defines the practical engineering workflow for planning, implementing, testing, reviewing, and documenting changes to madObsidian.
2. **Part 2** outlines the prioritized roadmap of upcoming work, separating immediate bugfixes from near-term hardening and deferred long-term proposals.

---

## Part 1: Practical Development Workflow

The workflow is intentionally lightweight — enough structure to preserve data safety and architectural coherence without unnecessary bureaucracy.

### 1.1 Document Roles and Ownership

| Document | Role | Update Trigger |
| --- | --- | --- |
| [README.md](../README.md) | Entry point: quick start, CLI summary, setup, doc map. | Setup, CLI commands, or top-level user workflow changes. |
| [CHANGELOG.md](../CHANGELOG.md) | Chronological record of user-visible & technical changes per release. | Every release (and `[Unreleased]` during development). |
| [SPECIFICATION.md](SPECIFICATION.md) | Authoritative current & intended behavior, formats, config, architecture. | Any behavioral, schema, CLI, config, or architecture change. |
| [DECISIONS.md](DECISIONS.md) | Durable ADRs capturing *why* major design choices were made. | Adding, revising, or reversing an architectural decision. |
| [PROGRESS.md](PROGRESS.md) | Current status, test inventory, known bugs, tech debt, open questions. | Fixing known issues, discovering new bugs, completing milestones. |
| [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md) | Project-wide invariants and verification gates. | New invariant introduced or existing guarantee refined. |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Workflow + prioritized roadmap of upcoming workstreams. | Completing roadmap items or re-prioritizing work. |
| [AGENTS.md](../AGENTS.md) | Operational guardrails for AI coding agents (Git safety, approvals, boundaries, auto-doc-sync). | Only when agent operating rules change — keep product specs out of `AGENTS.md`. |

### 1.2 Change Lifecycle

1. **Understand & Scope**
   - Read relevant sections of [SPECIFICATION.md](SPECIFICATION.md), [DECISIONS.md](DECISIONS.md), and [PROGRESS.md](PROGRESS.md) alongside the affected source and test files.
   - Check `git status` and preserve all pre-existing user work and local artifacts.
2. **Plan (for non-trivial changes)**
   - State the intended behavior, affected files, edge cases, and specific acceptance criteria (referencing [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md)).
   - Resolve ambiguities before editing code. Do not treat deferred proposals in `PROGRESS.md` or Part 2 below as pre-approved requirements.
3. **Implement Focused Changes**
   - Keep module boundaries intact (`cli`, `config`, `importer`, `exclusions`, `locking`, `previews`, `video_previews`, `archive`, `filenames`).
   - Avoid unrelated refactoring or new runtime dependencies without explicit rationale.
4. **Test Thoroughly**
   - Add or update focused unit/integration tests for every behavioral change.
   - Run narrowest affected test modules first, then run the **entire** test suite (`python -m unittest discover -s tests -v`) to catch cross-module regressions (such as the `test_config.py` mock breakage missed during `0.2.1`).
   - Run `git diff --check`.
5. **Review**
   - Verify changes against [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md) — especially data integrity, rollback safety, symlink/path safety, concurrency, and Windows + Raspberry Pi/Linux portability.
   - Distinguish executed tests from static checks and report exact commands and outcomes.
6. **Update Documentation & Release**
   - Update affected `docs/` files, `CHANGELOG.md`, and `README.md` if needed.
   - For releases: verify version parity across `pyproject.toml`, `src/madobsidian/__init__.py`, and `tests/test_cli.py`, run the full test suite, update `CHANGELOG.md`, and create an annotated Git tag (`vX.Y.Z`) pointing to the final release commit (starting with `v0.2.2`; a release may consist of multiple commits).

---

## Part 2: Prioritized Roadmap

Items below are **planned or proposed workstreams**, not completed features. None are approved for implementation by appearing here; every item still requires explicit user approval before implementation.

Each roadmap item is classified to distinguish urgency and maturity:
- **`[Required correction]`** — Confirmed defect or correctness issue whose behavior should be corrected (requiring an explicit behavior decision first where noted).
- **`[Hardening]`** — Reliability, safety, portability, process, or test-coverage improvement over working behavior.
- **`[Design proposal]`** — New capability or scope change whose design must be explicitly approved before implementation.

### Phase 1: Immediate Stabilization & Hygiene (`0.2.2` candidates)

High-value, low-risk workstreams addressing concrete issues and improvements identified during the `0.2.1` audit:

1. **`[Hardening]` Add regression test for pre-hashing read failure exclusions (`tests/test_exclusions.py`)**
   - Cover the complete behavior when an `OSError` occurs during `_sha256` before transaction start: source preservation in `Incoming/`, unchanged Markdown notes, non-serious failure (`serious = False`), persistent exclusion written to `exclusions.ini` (`[ignore]`), and subsequent import run skipping the file without hashing.
2. **`[Required correction]` Clarify `--dry-run` result structure and output for new imports vs. pending recovery (`importer.py:883-885`, `cli.py:55`)**
   - Preferred API design (not merely a CLI formatting workaround): add a dedicated `batch.recoveries` attribute on `ImportBatchResult` so that:
     - The list contents represent planned imports in `--dry-run` mode and completed imports in a real run (`len(batch)` must not include recovery operations).
     - `batch.recoveries` holds pending recovery operations.
     - Existing `batch.skipped`, `batch.ignored`, and `batch.failures` remain separate categories.
     - The CLI reports planned imports and pending recoveries separately.
   - Planned acceptance criteria include updating affected dry-run tests and adding focused tests covering `--dry-run` output with pending recovery transactions.
3. **`[Design proposal]` Add configurable `ffmpeg` and `ffprobe` executable paths in `config.ini` (`config.py`, `video_previews.py`, `archive.py`, `cli.py`, `config.example.ini`)**
   - Add an optional dedicated `[tools]` section (portable defaults in `config.example.ini`):
     ```ini
     [tools]
     ffmpeg = auto
     ffprobe = auto
     ```
   - Enforce independent per-executable resolution precedence:
     1. Corresponding environment variable (`MADOBSIDIAN_FFMPEG` / `MADOBSIDIAN_FFPROBE`).
     2. Explicit value in `[tools]`.
     3. Automatic discovery through `PATH`.
   - `auto` means automatic `PATH` discovery and must be **lazy**: `load_config()` must not fail merely because FFmpeg or ffprobe is unavailable; an error is appropriate only when the executable is actually needed.
   - Explicit configuration must be validated and fail clearly if invalid, with no silent fallback to another source:
     - Relative paths containing path components resolve relative to `config_path.parent` (matching `[watermark] font`).
     - Absolute paths are supported.
     - Bare executable names without path components (for example, `my-ffmpeg`) resolve via `shutil.which()`, reporting an explicit configuration error if not found.
   - Apply the same resolution logic consistently across `import`, `verify`, and `rebuild-index` wherever FFmpeg/ffprobe is invoked.
   - Include unit tests covering precedence, lazy `auto` behavior, bare executable names, relative and absolute paths, invalid explicit paths, and missing executables.
4. **`[Required correction]` Skip custom font validation when `watermark.enabled = false` (`config.py:172-183`)**
   - When `watermark.enabled = false`, a configured custom font does not need to exist or load.
   - Retain strict existence and Pillow load validation when watermarking is enabled.
   - Add unit tests covering both enabled and disabled states.
5. **`[Hardening]` Standardize release hygiene & Git tagging**
   - A release may consist of multiple commits; do not require a single-commit release.
   - Require version parity across `pyproject.toml`, `src/madobsidian/__init__.py`, and `tests/test_cli.py`, full 12-module test-suite verification, `CHANGELOG.md` update, `git diff --check`, and an annotated `vX.Y.Z` tag pointing to the final release commit.
   - Begin consistent tagging with `v0.2.2`; do not retroactively tag `0.2.1`.

### Phase 2: Robustness & Cross-Platform Hardening (`0.3.x` candidates)

Ordered by priority, derived from codebase inspection and the former `README.md` TODO backlog:

1. **`[Hardening]` Transaction journal & power-loss hardening (`importer.py`, `archive.py`, `exclusions.py`)**
   - Strengthen semantic validation of `transaction.json` and constrain recovery filesystem changes further.
   - Add directory-level `fsync` after atomic renames on POSIX (Linux / Raspberry Pi).
   - Document a clear operator recovery runbook for unrecoverable staging/journal conflicts.
2. **`[Hardening]` / `[Design proposal]` Concurrency coordination for `verify`/`rebuild-index` and legacy `import_path` decision (`archive.py`, `importer.py`, `locking.py`)**
   - `[Hardening]` Add locking or explicit concurrency coordination for `verify` and `rebuild-index` so they do not report transient `.incomplete-*` stages as archive problems during an active `import_vault`.
   - `[Design proposal]` Decide whether legacy two-path `import_path` should acquire the archive/vault lock, remain unlocked as legacy compatibility, or be deprecated.
3. **`[Required correction]` NFC-equivalent basename ambiguity (`importer.py:878-882`, `tests/test_vault_import.py:155-179`)**
   - **Correctness requirement (kept in Phase 2; do not pull into `0.2.2` without separate approval):** Currently, when multiple NFC-equivalent files in `Incoming/` make a basename-only Markdown reference ambiguous, both files are moved into `objects/` while the Markdown reference is left unchanged, which can leave links unresolved.
   - **Agreed target behavior:**
     - Leave all ambiguous NFC-equivalent candidates in `Incoming/`; do not move either candidate into `objects/`.
     - Report the ambiguity clearly as skipped/ambiguous so the user can rename a file or qualify the Markdown reference (`Incoming/...`) and rerun the importer.
     - Add tests proving neither candidate is moved and the Markdown reference remains unchanged, and update [SPECIFICATION.md](SPECIFICATION.md).
4. **`[Hardening]` Archive scan completeness & resource safety (`archive.py`, `previews.py`)**
   - Detect and report unexpected non-UUID directories/entries inside `objects/` (`archive.py:314-317`) instead of silently skipping them during `verify` and `rebuild-index`.
   - Guard Pillow decoding and verification against decompression bombs and resource-exhaustion inputs.
5. **`[Hardening]` Cross-platform filename portability (`filenames.py`, `exclusions.py`)**
   - Define and enforce rules for Windows-incompatible filenames (reserved characters `< > : " | ? *`, reserved device names, trailing dots/spaces) and ensure `exclusions.py` rejects backslashes consistently on Linux.
6. **`[Hardening]` Markdown context hardening (`importer.py`)**
   - Exclude YAML frontmatter from link rewriting.
   - Fix inline-code backtick scanning so fenced code block delimiters cannot pair across block boundaries.
7. **`[Design proposal]` / `[Hardening]` CLI `status` (`cli.py:44-46`) and targeted test-coverage improvements**
   - `[Design proposal]` Replace the static `ready (no archive configured)` placeholder in `madObsidian status` with real vault/archive inspection (e.g., pending transactions, ignored count, lock presence, active config) or explicitly formalize its minimal scope.
   - `[Hardening]` Address high-value test-coverage gaps identified in the baseline audit (deterministic lock-contention/exception-release tests, recovery-before-exclusions ordering, `exclusions.py` malformed/snapshot-conflict edge cases, CLI reporting paths, and rollback/recovery edge branches) and audit all 12 test modules for isolation and Windows/Linux parity.
8. **`[Hardening]` Syncthing deployment validation**
   - Harden and systematically test `.stignore` ingestion and multi-device synchronization rules (Windows + Raspberry Pi + VPS + Android).

### Phase 3: Ecosystem & Deferred Proposals (Requires Explicit Reopening)

All items below are deferred `[Design proposal]`s and must not be implemented unless explicitly reopened:

- **`[Design proposal]` Obsidian plugin & `madobsidian://<UUID>` URI handler:** Resolve original files on demand from the local archive or remote host.
- **`[Design proposal]` Cache manager & plugin-owned pinning (`[pin]`):** Retention policy for keeping selected originals on otherwise preview-only devices.
- **`[Design proposal]` Multi-vault & replacement-file `exclusions.ini` scoping (`exclusions.py`):** Evaluate whether `exclusions.ini` should remain shared per `config.ini` and keyed by basename only (requiring manual removal when replacing a broken file under the same name or sharing a config across vaults), or support vault-scoped / richer exclusion rules in the future.
