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
| `AGENTS.md` *(local)* | Operational guardrails for AI coding agents (Git safety, approvals, boundaries). | Only when agent operating rules change — keep product specs out of `AGENTS.md`. |

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
   - For releases: verify version parity across `pyproject.toml`, `src/madobsidian/__init__.py`, and `tests/test_cli.py`, run the full test suite, and create an annotated Git tag (`vX.Y.Z`) alongside the release commit.

---

## Part 2: Prioritized Roadmap

Items below are **planned or proposed workstreams**, not completed features. None should be implemented without explicit user approval.

### Phase 1: Immediate Stabilization & Hygiene (`0.2.2` candidates)

High-value, low-risk fixes addressing concrete bugs and inconsistencies identified during the `0.2.1` audit:

1. **Fix `tests/test_config.py::test_vault_cli_config_option_is_forwarded`**
   - Return an `ImportBatchResult()` (instead of `[]`) from the mocked `import_vault` so the full test suite passes cleanly.
   - Run and verify the entire 12-module `unittest discover -s tests -v` suite.
2. **Add regression test for pre-hashing read failure exclusions**
   - Add a focused test in `tests/test_exclusions.py` verifying that an `OSError` during `_sha256` leaves the `Incoming/` file and Markdown links untouched, reports a non-serious failure, and records the filename in `exclusions.ini` `[ignore]`.
3. **Clarify or fix `--dry-run` summary count (`importer.py:883-885`, `cli.py:55`)**
   - Separate pending recovery counts from planned new import counts in CLI summary output so dry-run reports accurately instead of conflating both under `"Would import N file(s)"`.
4. **Decide custom font validation behavior when `watermark.enabled = false` (`config.py:172-183`)**
   - Either skip custom font existence/Pillow loading when watermarking is disabled, or explicitly confirm and document unconditional startup validation with a dedicated test case.
5. **Standardize release hygiene & Git tagging**
   - Enforce a single-commit release checklist (version parity across `pyproject.toml`, `src/madobsidian/__init__.py`, and `tests/test_cli.py`, full 12-module test run, `git diff --check`) and create annotated Git tags (`vX.Y.Z`) for future releases (optionally deciding whether to tag `0.2.1` retroactively and whether to track local `AGENTS.md`).

### Phase 2: Robustness & Cross-Platform Hardening (`0.3.x` candidates)

Derived from codebase inspection and the former `README.md` TODO backlog:

1. **Archive scan completeness & resource safety (`archive.py`, `previews.py`)**
   - Detect and report unexpected non-UUID directories/entries inside `objects/` (`archive.py:314-317`) instead of silently skipping them during `verify` and `rebuild-index`.
   - Guard Pillow decoding and verification against decompression bombs and resource-exhaustion inputs.
2. **Extend or clarify concurrency locking beyond `import_vault` (`archive.py`, `importer.py`, `locking.py`)**
   - Add locking or explicit concurrency coordination for `verify` and `rebuild-index` so they do not report transient `.incomplete-*` stages as archive problems during an active `import_vault`.
   - Decide whether legacy two-path `import_path` should acquire the archive/vault lock, remain unlocked as legacy compatibility, or be deprecated.
3. **Revisit ambiguous NFC-equivalent basename import behavior (`importer.py:878-882`, `tests/test_vault_import.py:155-179`)**
   - Currently, when two NFC-equivalent files in `Incoming/` trigger an ambiguity on a basename-only Markdown reference, the note link is left unchanged while *both* files are still moved into `objects/`. Decide whether ambiguous candidates should remain in `Incoming/` until disambiguated (leaving the vault links working) or keep current behavior, and align implementation, tests, and [SPECIFICATION.md](SPECIFICATION.md).
4. **Cross-platform filename portability (`filenames.py`, `exclusions.py`)**
   - Define and enforce rules for Windows-incompatible filenames (reserved characters `< > : " | ? *`, reserved device names, trailing dots/spaces) and ensure `exclusions.py` rejects backslashes consistently on Linux.
5. **Markdown context hardening (`importer.py`)**
   - Exclude YAML frontmatter from link rewriting.
   - Fix inline-code backtick scanning so fenced code block delimiters cannot pair across block boundaries.
6. **Transaction journal & power-loss hardening (`importer.py`, `archive.py`, `exclusions.py`)**
   - Strengthen semantic validation of `transaction.json` and constrain recovery filesystem changes further.
   - Add directory-level `fsync` after atomic renames on POSIX (Linux / Raspberry Pi).
   - Document a clear operator recovery runbook for unrecoverable staging/journal conflicts.
7. **CLI `status` implementation (`cli.py:44-46`) & full test-suite audit**
   - Replace the static `ready (no archive configured)` placeholder in `madObsidian status` with real vault/archive inspection (e.g., pending transactions, ignored count, lock presence, active config) or explicitly formalize its minimal scope.
   - Perform a structured review of all 12 test modules for isolation, edge-case coverage, and Windows/Linux parity.
8. **Syncthing deployment validation**
   - Harden and systematically test `.stignore` ingestion and multi-device synchronization rules (Windows + Raspberry Pi + VPS + Android).

### Phase 3: Ecosystem & Deferred Proposals (Requires Explicit Reopening)

- **Obsidian plugin & `madobsidian://<UUID>` URI handler:** Resolve original files on demand from the local archive or remote host.
- **Cache manager & plugin-owned pinning (`[pin]`):** Retention policy for keeping selected originals on otherwise preview-only devices.
- **Multi-vault & replacement-file `exclusions.ini` scoping (`exclusions.py`):** Evaluate whether `exclusions.ini` should remain shared per `config.ini` and keyed by basename only (requiring manual removal when replacing a broken file under the same name or sharing a config across vaults), or support vault-scoped / richer exclusion rules in the future.
