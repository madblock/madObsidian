
# madObsidian — Agent Instructions

## 1. General operating principles

- Inspect the relevant code and existing project documentation before proposing changes.
- Do not modify files unless the user has explicitly requested an implementation or edit.
- An explicit user request to implement a specific change authorizes that requested work; do not ask for redundant permission.
- For non-trivial tasks, first present a plan identifying the files likely to be affected and wait for the user's approval before implementation.
- Approval for a task does not authorize unrelated changes or destructive operations.
- Keep changes minimal and consistent with the existing architecture and coding style.
- Do not perform unrelated refactoring, formatting, renaming, or cleanup.
- Explain the reason for any proposed dependency addition before introducing it.
- Never commit, push, reset, clean, or rewrite Git history without explicit permission.

## 2. Protect existing work

- Inspect `git status` before making changes.
- Treat all pre-existing modifications and untracked files as user-owned work.
- Never overwrite, delete, revert, stash, or otherwise interfere with pre-existing user changes.
- Do not assume untracked files are disposable build artifacts.
- Before editing a file, inspect its current contents and relevant Git diff.
- After implementation, report every modified, added, and deleted file.

## 3. Execution and approvals

- Read-only inspection commands (such as reading files, searching code, listing directories, checking Git status, and reading older files contents from Git, including diff) are permitted without additional approval.
- Before executing commands that modify files, install packages, change system configuration, or access remote services, assess their side effects and request approval when they are outside the explicitly authorized task.
- Commands that execute project code, tests, scripts, or external programs must be assessed for side effects; request approval when their effects are not clearly within the authorized task.
- Do not use automatic approval or unrestricted execution.
- Explain potentially destructive commands before proposing them.
- Do not run commands that delete, overwrite, or move user data without explicit approval.
- Never expose API keys, credentials, tokens, or other secrets in terminal output or reports.

## 4. Implementation and testing

- Prefer focused changes over broad rewrites.
- Add or update tests when behavior changes.
- Run the narrowest relevant tests first, followed by the broader test suite when appropriate.
- Report the exact commands executed and their results.
- Distinguish tests actually executed from tests merely recommended.
- Do not claim that a test passed unless it was executed and passed.
- Do not modify tests merely to make an implementation pass without explaining why.

## 5. Python project conventions

- Follow the existing Python version and package configuration.
- Preserve compatibility with supported Windows and Raspberry Pi/Linux environments.
- Use the project's existing virtual environment and development dependencies.
- Propose adding modules to existing configuration only if it's necessary and would make app's code simpler.
- Follow existing type annotations, error-handling conventions, and test structure.
- Avoid unnecessary third-party dependencies.

## 6. madObsidian project-specific constraints

- Preserve the existing separation of responsibilities between CLI, importer, media processing, and vault integration.
- Preserve per-file failure reporting, rollback/recovery, and continuation behavior.
- Do not reintroduce automatic JSON-based failed-file exclusions or related list/clear CLI commands.
- Preserve existing media and metadata unless the task explicitly requires changing them.

## 7. Communication

- Work incrementally and explain each meaningful step.
- Ask one focused clarification when a requirement is ambiguous.
- Before implementation, state the intended behavior and affected files.
- After implementation, summarize changes, tests, remaining limitations, and relevant Git status.
- Stop and ask if implementation would require a decision outside the user's stated requirements.
