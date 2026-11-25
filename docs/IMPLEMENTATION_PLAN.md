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

### Phase 1: Immediate Stabilization & Hygiene (`0.2.2` — Implemented, Pending Release Commit & `v0.2.2` Tag)

All five Phase 1 workstreams have been implemented and verified in the working tree for `0.2.2` (Git commit, push, and annotated `v0.2.2` tag remain pending explicit user authorization):

1. `[x]` **`[Hardening]` Add regression test for pre-hashing read failure exclusions (`tests/test_exclusions.py`)**
   - Covered complete behavior for `_sha256` `OSError` before transaction start: source preservation in `Incoming/`, unchanged Markdown, non-serious failure (`serious = False`), `[ignore]` persistence, no published object, and skip-before-hash on rerun.
2. `[x]` **`[Required correction]` Clarify `--dry-run` result structure and output for new imports vs. pending recovery (`importer.py`, `cli.py`, `tests/test_vault_import.py`)**
   - Added dedicated `ImportBatchResult.recoveries` attribute so `len(batch)` counts planned/completed imports only while `batch.recoveries` holds pending dry-run recovery operations and CLI reports them separately; updated existing dry-run tests and added coverage for dry-run with/without pending recoveries and real-import parity.
3. `[x]` **`[Design proposal]` Add configurable `ffmpeg` and `ffprobe` executable paths in `config.ini` (`config.py`, `video_previews.py`, `archive.py`, `cli.py`, `config.example.ini`, `tests/test_config.py`)**
   - Implemented optional `[tools]` section (`ffmpeg = auto`, `ffprobe = auto`) with independent precedence (`MADOBSIDIAN_FFMPEG`/`MADOBSIDIAN_FFPROBE` → `[tools]` → `PATH`), lazy `auto` `PATH` discovery, fail-closed explicit validation (relative to `config_path.parent`, absolute paths, `shutil.which()` for bare names, POSIX `os.X_OK` check) with no fallback, consistent integration across `import`, `verify`, and `rebuild-index`, and full unit tests.
4. `[x]` **`[Required correction]` Skip custom font validation when `watermark.enabled = false` (`config.py`, `tests/test_config.py`)**
   - Skipped custom font existence/Pillow loading when watermarking is disabled while retaining strict validation when enabled; added tests for both states.
5. `[x]` **`[Hardening]` Standardize release hygiene & Git tagging (`pyproject.toml`, `src/madobsidian/__init__.py`, `tests/test_cli.py`, `CHANGELOG.md`, `docs/`)**
   - Synchronized version `0.2.2`, updated `CHANGELOG.md` and engineering docs, verified full suite and `git diff --check`; ready for final release commit(s) and annotated `v0.2.2` tag once explicitly authorized (no retroactive tag for `0.2.1`).

### Phase 2: Robustness & Cross-Platform Hardening (`0.3.x` candidates)

Phase 2 runs on `develop` with tested checkpoints. The implementation sequence covers journal and crash recovery, one vault import API and coordinated locking, NFC ambiguity, shared path exclusions and Incoming resolution, bounded archive verification, portable storage names, Markdown context, operational status, and isolated Syncthing integration. See [PROGRESS.md](PROGRESS.md) for executed checks and outstanding platform verification.

### Phase 3: Ecosystem & Deferred Proposals (Requires Explicit Reopening)

All items below are deferred `[Design proposal]`s and must not be implemented unless explicitly reopened:

- **`[Design proposal]` Obsidian plugin & `madobsidian://<UUID>` URI handler:** Resolve original files on demand from the local archive or remote host.
- **`[Design proposal]` Cache manager & plugin-owned pinning (`[pin]`):** Retention policy for keeping selected originals on otherwise preview-only devices.
- Multi-vault orchestration remains deferred.
