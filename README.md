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
madObsidian status
madObsidian import <vault-directory> [--dry-run] [--config config.ini]
madObsidian import <file-or-directory> <archive-directory> [--config config.ini]  # legacy copy mode
madObsidian verify <vault-or-archive-root> [--config config.ini]
madObsidian rebuild-index <vault-or-archive-root> [--config config.ini]
```

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```

### Key Usage Notes

- **Vault mode (`import <vault>`):** Moves referenced, direct-child media files from `Incoming/` into `objects/<uuid>/{shared|originals}/`, rewrites Markdown wikilinks/embeds (adding `madobsidian://<UUID>` original links for large files), serializes runs with a per-vault lock in `vault.parent`, logs failures to `vault.parent/import.log`, and records safely rolled-back failures in `exclusions.ini` (`[ignore]`). Supports `--dry-run` with unified Markdown diffs and separate pending-recovery counts.
- **Legacy mode (`import <src> <archive>`):** Copy-only importer for compatibility; does not modify sources, rewrite Markdown, use transactions/exclusions, or accept `--dry-run`.
- **Verification (`verify` / `rebuild-index`):** Pass the vault or archive root containing `index.json` (not `objects/` itself), along with the same `--config` if custom `objects_directory` or `small_file_threshold_bytes` settings are used. `rebuild-index` refuses to replace `index.json` if any archive problem is found.
- **Failures and retries:** If read of large file fails at any point, it leaves the file and corresponding links in .md files untouched and makes an entry in `exclusions.ini` under `[ignore]` section in order not to encounter the failure next time. If the user want the app to revisit the large file, it should remove the corresponding entry from the `exclusions.ini`

## Configuration

Copy [`config.example.ini`](config.example.ini) to a local `config.ini` (ignored by Git) and pass `--config config.ini`. When `--config` is omitted, built-in defaults matching `config.example.ini` are used. Configuration is strictly validated at startup (unknown keys, invalid ranges, unsupported formats/codecs, and unreadable fonts fail fast).

Default key settings:

- `[general]`: `incoming_directory = Incoming`, `objects_directory = objects`, `small_file_threshold_bytes = 256000`
- `[images]`: `max_dimension = 960`, `format = webp`, `quality = 85`, `method = 6`
- `[videos]`: `max_dimension = 400` (even), `format = mp4`, `video_codec = libx264`, `video_crf = 25`, `preset = medium`, `audio_codec = aac`
- `[watermark]`: `enabled = true`, `font = bundled` (DejaVu Sans 2.37; custom fonts are validated only when `enabled = true`), `font_size = auto`, `opacity = auto`, `position = bottom-left`, `margin = auto`
- `[tools]`: `ffmpeg = auto`, `ffprobe = auto` (optional; `auto` discovers lazily on `PATH` only when needed)

See [docs/SPECIFICATION.md](docs/SPECIFICATION.md) for full configuration rules, path resolution, and `exclusions.ini` details.

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

Machine-local coordination files (`import.log`, `.madobsidian-vault-<sha256>.lock`, `exclusions.ini`, and `exclusions.ini.lock`) are stored outside the synchronized vault directory.

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
