# madObsidian

madObsidian is the starting point for a personal media archive and Obsidian
integration. The importer preserves original files with UUIDs and metadata,
and creates WebP previews for sufficiently large raster images.

## Development setup

Requires Python 3.11 or newer. On Windows, create and activate the local
virtual environment with:

```powershell
C:\Python311\python.exe -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Run the CLI and tests with:

```powershell
madObsidian --help
madObsidian --version
madObsidian status
madObsidian import <vault-directory> [--dry-run] [--config config.ini]
madObsidian import <file-or-directory> <archive-directory>  # legacy copy mode
madObsidian verify <vault-or-archive-root> [--config config.ini]
madObsidian rebuild-index <vault-or-archive-root> [--config config.ini]
python -m unittest discover -s tests -v
```

`config.example.ini` documents the processing defaults. Copy it to a local
`config.ini` and pass `--config config.ini` to use it; `config.ini` is ignored by
Git. When `--config` is omitted, the same built-in defaults are used. The import
command accepts the option in both vault mode and the legacy copy-only mode.
Pass the same configuration to `verify` and `rebuild-index` when the archive
uses a non-default objects directory or file-size threshold. For `verify` and
`rebuild-index`, pass the vault/archive root that contains `index.json`; do not
pass the `objects/` directory itself. The legacy two-path import also accepts
`--config`, resolving its configured `objects_directory` beneath the positional
archive root. `incoming_directory` is only used by vault import.

`--dry-run` applies only to the one-path vault import form. The legacy
copy-only form has no dry-run mode and rejects `--dry-run` rather than copying.

### Processing configuration

The INI file has four groups. Directory settings are used by vault import;
relative directory values resolve from the vault root and must remain inside the
vault. Absolute paths are accepted when they point inside that vault. The paths
in the example are portable and contain no machine-specific locations.

| Group and setting | Default | Effect |
| --- | --- | --- |
| `[general] incoming_directory` | `Incoming` | Directory scanned for import candidates. Only files directly inside it are imported. |
| `[general] objects_directory` | `objects` | Directory where UUID objects are stored. |
| `[general] small_file_threshold_bytes` | `256000` | Files below this size use `shared/` and have no preview; files at or above it use `originals/` and receive eligible media previews. |
| `[images] max_dimension` | `960` | Longest side of an image preview in pixels; images are never enlarged. |
| `[images] format` | `webp` | Image preview format. Currently supported: `webp`. |
| `[images] quality` | `85` | Lossy WebP quality from 1 to 100. Transparency is saved losslessly. |
| `[images] method` | `6` | WebP encoder effort from 0 (fastest) to 6 (smallest output). |
| `[videos] max_dimension` | `400` | Longest side of a video preview in pixels; must be even for MP4 output. |
| `[videos] format` | `mp4` | Video preview container. Currently supported: `mp4`. |
| `[videos] video_codec` | `libx264` | FFmpeg video encoder. Currently supported: `libx264`. |
| `[videos] video_crf` | `25` | H.264 quality/compression setting from 0 (highest quality/largest) to 51 (lowest quality/smallest). |
| `[videos] preset` | `medium` | FFmpeg x264 speed/compression preset, from `ultrafast` through `veryslow`. |
| `[videos] audio_codec` | `aac` | Audio encoder for available source audio. Currently supported: `aac`. |
| `[watermark] enabled` | `true` | Enables or disables preview watermarks. |
| `[watermark] font` | `bundled` | Font file used for preview watermarks. `bundled` selects the included DejaVu Sans font. |
| `[watermark] font_size` | `auto` | Font size in pixels (6–256), or `auto` to retain the existing image/video-specific sizing. |
| `[watermark] opacity` | `auto` | Watermark band opacity (0–255), or `auto` to retain existing image/video defaults. |
| `[watermark] position` | `bottom-left` | Watermark placement: `bottom-left`, `bottom-right`, `top-left`, or `top-right`. |
| `[watermark] margin` | `auto` | Padding in pixels (0–1024), or `auto` to retain existing media-specific margins. |

Font paths that are not `bundled` may be absolute or relative. A relative font
path is resolved from the directory containing the selected INI file, not from
the current working directory. Configuration is validated before processing;
unknown keys, invalid values, unsupported formats/codecs, and unreadable fonts
are reported with the configuration path and setting name. The current preview
implementation intentionally supports only the formats and codecs listed
above; unsupported alternatives fail at startup rather than being ignored.
For a portable Linux deployment, use `font = bundled` and one of the supported
positions such as `bottom-left`; a bare `bottom` value is not accepted.

Video previews require FFmpeg with the `libx264` encoder and `ffprobe` on
`PATH`. Executable paths can be set with `MADOBSIDIAN_FFMPEG` and
`MADOBSIDIAN_FFPROBE`; each variable may contain either a path or an executable
name resolvable through `PATH`. Archive verification also decodes eligible video
previews, so `verify` and `rebuild-index` need these tools when such objects exist.

Vault import reads regular files directly under the configured Incoming
directory only; it never imports arbitrary vault files or nested directories.
It records the source hash, moves the source into an unpublished staging object
using `Path.rename()`, verifies the moved file, then generates previews and metadata.
The source and destination must be on the same filesystem. If the rename fails,
the import is aborted: the original remains in Incoming, no object is published,
and Markdown is not modified.

Failed imports are logged to `import.log` beside the vault directory. In a
vault batch, failures are isolated per file. The importer attempts safe
transaction rollback and verifies that the original's size and SHA-256 match at
its original Incoming path. After that verification, it records the filename in
`exclusions.ini` so the same file is ignored on subsequent runs. If rollback or
integrity verification fails, the staging object and transaction journal are
retained for recovery and no exclusion is written. A published object whose
Markdown finalization is incomplete also remains journaled for recovery and is
never excluded. Other eligible files continue importing after a failure.
Remove the filename's line from `exclusions.ini` manually to re-enable retry.
The import summary reports imported, skipped, ignored, and failed counts;
ignored files are listed separately from ordinary eligibility skips. A batch
with any failed file returns a nonzero CLI status, even when other files were
imported. Recovery errors require successful transaction recovery before the
affected file can be safely retried.
For compatibility, the two-argument form remains a copy-only legacy importer.

### Persistent failed-import exclusions

`exclusions.ini` is stored beside the selected `config.ini`. When no `--config`
is supplied, it is stored in the vault's parent directory. The file uses a
separate `[ignore]` section with a SHA-256 key derived from the exact UTF-8
filename and a JSON-encoded filename value:

```ini
[ignore]
ed2e575a1d298e08df19dca9733da3f46eab5b157b1b8f9ed02f31b2f907ec79 = "example.jpg"
```

The importer validates each key against its JSON filename and fails safely if
the file is malformed. It loads exclusions after transaction recovery, then
skips matching filenames before reading their contents, generating previews,
planning Markdown edits, or attempting import. These are shown as ignored, not
as ordinary skips such as hidden, unsupported, or unreferenced files. Exclusions
are append-only from the importer's perspective; manually remove a line to
permit another attempt. The importer never excludes a source if rollback is
incomplete, its original cannot be verified, or its object was already
published. Exclusion updates reload under a cross-process lock and use an
atomic replacement so concurrent updates do not lose entries.

Vault imports are serialized by a cross-process lock whose name includes the
SHA-256 hash of the resolved vault path. The persistent lock file is placed in
`root.parent`, outside the synchronized vault, and is not removed after release.
Acquiring it may create this small coordination file. Dry-run may create that
lock file, but does not modify the vault, selected configuration file, or
`exclusions.ini`.

Vault objects use this layout (empty original directories are omitted):

```text
objects/<uuid>/
|-- metadata.json
|-- shared/<original filename>       # smaller than 256,000 bytes
|-- originals/<original filename>    # 256,000 bytes or larger
`-- previews/preview.webp or preview.mp4
```

Metadata field `original_path` records the path relative to the object directory.
The existing preview threshold and image/video preview behavior are retained.
The `--dry-run` option reports planned object locations and changed notes without
writing, copying, or moving files. UUIDs in the dry-run are proposed IDs.
They are illustrative and may differ from UUIDs assigned by a later actual import.

Markdown wikilinks and embeds are matched throughout the vault. An explicit
`Incoming/filename` target keeps exact-path matching. A basename-only target
such as `![[filename.jpg]]` is rewritten only when it matches exactly one
eligible file directly inside the current `Incoming/` import set. If multiple
eligible files match, the reference is reported as ambiguous and left unchanged;
references with no eligible match are also left unchanged. Large files point to
their preview and receive a stable `(original)` link using
`madobsidian://<UUID>`; small files point to their `shared/` original. Aliases
and heading fragments are retained. Standard Markdown links, Obsidian block
references, and nested Incoming folders are not rewritten automatically.

Each staging and published vault object has a `transaction.json` journal. It
contains targeted, counted link replacements rather than saved note snapshots.
Recovery reapplies replacements to current note text, preserving unrelated
changes, and stops with a conflict if the references themselves changed.
Re-running `import` with the same directory and threshold settings recovers
unfinished work after checking recorded hashes. Before publication, recovery
returns a verified moved original from staging to Incoming and discards the
unpublished stage; after publication, it replays the targeted note edits and
finalizes the journal. Recovery is repeatable. If both copies exist, it retains
the verified Incoming copy before discarding staging; if neither verifies, it
preserves the stage and stops for investigation. `verify` and
`rebuild-index` reject unfinished journals and incomplete objects. Markdown code
blocks and inline code are left unchanged; path and basename comparisons use
Unicode NFC normalization. Markdown newline style is preserved.

The post-move hash check detects changes that occurred before or during the
move, and the original is checked again after preview processing. It cannot
prevent another process that already has the file open from writing through
that handle after a check; such writes remain a filesystem race and are not
made impossible by rename.

Keep the same `incoming_directory`, `objects_directory`, and
`small_file_threshold_bytes` settings until any interrupted import has been
recovered. The importer refuses to start a new import if it finds a pending
transaction or staging directory outside the currently configured objects
directory. If a pending journal is found with a changed Incoming path or size
threshold, restore the configuration used for that transaction and rerun import.

On VPS and Android, use these local Syncthing ignore rules to retain metadata,
previews, and small shared files while excluding large originals:

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

Add `objects/.incomplete-*` to `.stignore` on every synchronized device,
including the Raspberry Pi, so unpublished staging transactions are not
synchronized. This rule affects synchronization only; it does not recover or
clean up local transactions. Windows and Raspberry Pi otherwise synchronize all
archive files. These rules are documentation only; madObsidian does not
configure remote devices.

Legacy imports are copied into a temporary directory under `objects`, verified by
SHA-256, and accompanied by metadata before the directory is renamed to its
UUID. Failed legacy imports are not reported as completed objects. The legacy
copy-only importer does not modify source files.

Images of 256,000 bytes or more receive a WebP preview at
`previews/preview.webp` inside the object directory. Previews are oriented
according to EXIF, preserve aspect ratio, are not enlarged, and fit within 960
pixels on their longest side. Transparency is retained when supported by the
source and WebP encoder. Smaller images and non-image files have no preview.
The preview watermark shows the original filename, size in decimal MB, and
orientation-corrected resolution. Preview generation is part of the temporary
object import: if generation fails for an eligible image, the object is not
published.

Video candidates are identified by MIME type and a common-extension fallback,
then confirmed by `ffprobe`. Videos of 256,000 bytes or more receive a full
length H.264 MP4 preview at `previews/preview.mp4`, scaled to at most 400 pixels
on the longest side. The preview includes the first audio stream as AAC when
present, preserves VFR timing and relative audio/video synchronization, and
watermarks the original filename, size, display resolution, and video-stream
duration. VFR videos also include average FPS. Video previews are generated
before publication; any probe, encoding, or verification failure aborts that
file's import.

Pillow is the only runtime dependency. The bundled DejaVu Sans font is from
DejaVu Fonts 2.37; its license is included in
`src/madobsidian/assets/DEJAVU-LICENSE.txt`. The DejaVu license permits
redistribution subject to including its copyright and permission notices
[as stated in the upstream license](https://github.com/dejavu-fonts/dejavu-fonts/blob/master/LICENSE).

`verify` checks each UUID object directory against its metadata and original
file without changing the archive. `rebuild-index` regenerates the disposable
`index.json` from valid per-object metadata. The index contains version `1` and
an `objects` list; each entry has a `metadata` record and an archive-relative
`path`. If any object is invalid, the existing index is left unchanged. The
per-object `metadata.json` files remain authoritative, so the index can be
deleted and rebuilt later. Abandoned `.incomplete-` directories are reported
for investigation and are never indexed.

## TODO

- Strengthen transaction-journal semantic validation and further constrain
  recovery filesystem changes to reduce the impact of malformed local state.
- Expand Markdown context handling for frontmatter and inline-code edge cases,
  where links may currently be ignored or interpreted unexpectedly.
- Define cross-platform filename restrictions and handling for names Windows
  cannot represent, so archives remain portable across devices.
- Detect and report unexpected non-UUID object directories, which can otherwise
  remain unnoticed during routine archive scans.
- Protect archive verification from decompression bombs and other
  resource-exhaustion inputs.
- Add appropriate file and directory `fsync` operations where supported to
  improve recovery after sudden power loss.
- Further harden and test Syncthing ingestion and synchronization rules to
  reduce the chance of accidental exclusion or unwanted propagation.
- Design and implement plugin-owned pinning as a future original-object
  retention policy in the cache manager.
- Add recovery guidance and an operator workflow for persistent failures where
  the journal or staged original cannot be verified automatically.
- Perform a comprehensive code review of the entire test suite, including
  coverage gaps, test isolation, edge cases, and consistency with documented
  importer behavior.
