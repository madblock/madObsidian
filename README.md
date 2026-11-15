# madObsidian

madObsidian is the starting point for a personal media archive and Obsidian
integration. The planned project will keep a permanent archive on a Raspberry
Pi, maintain a complete Windows copy, and provide media previews and original
file retrieval. This initial skeleton does not implement archive or media
features.

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
python -m unittest discover -s tests -v
```

The application itself currently uses only Python's standard library.
