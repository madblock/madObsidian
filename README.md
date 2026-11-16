# madObsidian

madObsidian is the starting point for a personal media archive and Obsidian
integration. Stage 1 provides a local importer for preserving original files
with UUIDs and metadata. It uses Python's standard library and does not inspect
file contents to identify media types.

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
not overwrite existing objects or modify source files. The application uses
only Python's standard library.

`verify` checks each UUID object directory against its metadata and original
file without changing the archive. `rebuild-index` regenerates the disposable
`index.json` from valid per-object metadata. The index contains version `1` and
an `objects` list; each entry has a `metadata` record and an archive-relative
`path`. If any object is invalid, the existing index is left unchanged. The
per-object `metadata.json` files remain authoritative, so the index can be
deleted and rebuilt later. Abandoned `.incomplete-` directories are reported
for investigation and are never indexed.
