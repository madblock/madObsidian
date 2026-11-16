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
