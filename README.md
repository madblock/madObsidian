# madObsidian

madObsidian is a local-first personal media archive and Obsidian vault integration tool (current release: `0.2.2`). It moves referenced media from an Obsidian vault's `Incoming/` directory into SHA-256-verified, UUID-addressed object directories, generates watermarked WebP image previews and full-length H.264/AAC MP4 video previews for large files, and rewrites vault Markdown wikilinks safely with crash recovery.

## Quick Start

Requires **Python 3.11+** and **Pillow (`>=10.0`)** (the sole Python runtime dependency). Video previews and verification of archives containing video previews also require `ffmpeg` (with `libx264` and `aac`) and `ffprobe`, resolved independently in precedence order: `MADOBSIDIAN_FFMPEG`/`MADOBSIDIAN_FFPROBE` environment variables → `[tools]` in `config.ini` → `PATH`.

On Windows (PowerShell):

```powershell
C:\Python311\python.exe -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

On Linux / Raspberry Pi:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## CLI Usage

```powershell
madObsidian --help
madObsidian --version
madObsidian status [<vault-directory>] [--config config.ini]
madObsidian import <vault-directory> [--dry-run] [--config config.ini]
madObsidian verify <vault-or-archive-root> [--config config.ini]
madObsidian rebuild-index <vault-or-archive-root> [--config config.ini]
```

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```

### Key Usage Notes

- **Vault mode (`import <vault>`):** Moves referenced media files under the resolved Incoming folder into `objects/<uuid>/{shared|originals}/`, rewrites Markdown wikilinks/embeds (adding `madobsidian://<UUID>` original links for large files), serializes runs with a per-vault lock in `vault.parent`, logs failures to `vault.parent/import.log`, and records safely rolled-back failures in `madobsidian_exclusions.ini` (`[ignore]`). Supports `--dry-run` with unified Markdown diffs and separate pending-recovery counts.
- **Verification (`verify` / `rebuild-index`):** Pass the vault or archive root containing `index.json` (not `objects/` itself), along with the same `--config` if custom `objects_directory` or `small_file_threshold_bytes` settings are used. `rebuild-index` refuses to replace `index.json` if any archive problem is found.
- A failed import with a verified source creates an entry in the vault's `madobsidian_exclusions.ini`. Remove the matching line to authorize a retry. An incomplete rollback retains the journal. A published object with an unfinished note update is finalized by recovery.
- Basename ambiguity leaves the candidates in Incoming. Qualify the reference or rename the files, then remove their exclusions. Portable renaming happens during the journaled move into staging.
- Use `status <vault>` for lightweight diagnostics, including recovery and exclusion counts. Use `verify` for content verification.

## Configuration

Copy [`config.example.ini`](config.example.ini) to a local `config.ini` (ignored by Git) and pass `--config config.ini`. When `--config` is omitted, built-in defaults matching `config.example.ini` are used. Configuration is strictly validated at startup (unknown keys, invalid ranges, unsupported formats/codecs, and unreadable fonts fail fast).

Obsidian's `.obsidian/app.json` setting `attachmentFolderPath` takes precedence over the INI fallback. The folder must resolve inside the vault. Missing folders are reported to the local import log and treated as empty. Status reports the missing path read-only.

Default key settings:

- `[general]`: `incoming_directory = Incoming`, `objects_directory = objects`, `small_file_threshold_bytes = 256000`
- `[images]`: `max_dimension = 960`, `format = webp`, `quality = 85`, `method = 6`
- `[videos]`: `max_dimension = 400` (even), `format = mp4`, `video_codec = libx264`, `video_crf = 25`, `preset = medium`, `audio_codec = aac`
- `[watermark]`: `enabled = true`, `font = bundled` (DejaVu Sans 2.37; custom fonts are validated only when `enabled = true`), `font_size = auto`, `opacity = auto`, `position = bottom-left`, `margin = auto`
- `[tools]`: `ffmpeg = auto`, `ffprobe = auto` (optional; `auto` discovers lazily on `PATH` only when needed)

See [docs/SPECIFICATION.md](docs/SPECIFICATION.md) for full configuration rules, path resolution, and `madobsidian_exclusions.ini` details.

## Multi-Device Syncthing Deployment

Run imports on one device at a time and wait for synchronization to finish before switching devices. The vault lock coordinates local processes only. If recovery reports a conflict, pause imports, preserve the entire stage or UUID directory and its journal, and compare the original and note contents before resolving the conflict.

Add `objects/.incomplete-*` to `.stignore` on **every** synchronized device (including Windows and Raspberry Pi) so unpublished staging directories are never synced.

On lightweight devices (VPS, Android), use these local `.stignore` rules to keep metadata, previews, and small `shared/` files while excluding `Incoming/` and large `originals/`:

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

Synchronize `madobsidian_exclusions.ini` at the vault root. The import log and both local lock files live beside the vault. Stop and resolve Syncthing conflict copies before resuming imports. A clean local scan describes files visible on that device. Check Syncthing convergence separately.

## Documentation

Engineering documentation lives in `docs/` and [`CHANGELOG.md`](CHANGELOG.md):

- **[docs/SPECIFICATION.md](docs/SPECIFICATION.md)** — Authoritative technical & functional specification (architecture, vault/object layouts, CLI, config, import lifecycle, Markdown rewriting, recovery, exclusions, locking, media previews, verification).
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — Architecture Decision Records (ADRs) explaining why key design choices were made and their trade-offs.
- **[docs/PROGRESS.md](docs/PROGRESS.md)** — Current project status, test suite inventory, known bugs/discrepancies, technical debt, deferred work, and open questions.
- **[docs/ACCEPTANCE_CRITERIA.md](docs/ACCEPTANCE_CRITERIA.md)** — Non-negotiable invariants and quality gates for all changes and releases.
- **[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)** — Practical development workflow and prioritized roadmap.
- **[CHANGELOG.md](CHANGELOG.md)** — Release history reconstructed from Git history and ongoing changes.

## Dependencies and Licensing

- **Pillow (`>=10.0`):** Only Python runtime dependency.
- **Bundled Font:** DejaVu Sans 2.37 (`src/madobsidian/assets/DejaVuSans.ttf`); license notices are included in [`src/madobsidian/assets/DEJAVU-LICENSE.txt`](src/madobsidian/assets/DEJAVU-LICENSE.txt) ([upstream license](https://github.com/dejavu-fonts/dejavu-fonts/blob/master/LICENSE)).
