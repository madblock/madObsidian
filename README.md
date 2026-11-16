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
madObsidian import <file-or-directory> <archive-directory>
madObsidian verify <archive-directory>
madObsidian rebuild-index <archive-directory>
python -m unittest discover -s tests -v
```

Video previews require FFmpeg with the `libx264` encoder and `ffprobe` on
`PATH`. Executable paths can be set with `MADOBSIDIAN_FFMPEG` and
`MADOBSIDIAN_FFPROBE`; each variable may contain either a path or an executable
name resolvable through `PATH`.

The importer recursively processes regular files in a directory. Each import
creates a new object, even when the same content was imported before. Objects
are stored as `<archive-directory>/objects/<uuid>/<original filename>` with a
`metadata.json` file containing the original filename, extension, MIME type,
size, SHA-256, UUID, and UTC import timestamp. MIME type is guessed from the
filename using `mimetypes`; unknown types are recorded as
`application/octet-stream`.

Files are copied into a temporary directory under `objects`, verified by
SHA-256, and accompanied by metadata before the directory is renamed to its
UUID. Failed imports are not reported as completed objects. The importer does
not overwrite existing objects or modify source files.

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
