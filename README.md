Release Context Builder
=======================

This small tool builds a Markdown and JSON summary of changes between two git refs using the GitHub API.

Usage
-----

1. Ensure `GITHUB_TOKEN` is available (in `.env` or exported).
2. Install dependencies:

```bash
python -m pip install python-dotenv requests tqdm
```

3. Run the script:

Using the project's `uv` runner (preferred):

```bash
uv run python src/github_release_context.py --repo owner/name --from v1.2.3 --to v1.3.0 --out-dir ./out
```

Or run directly with Python:

```bash
python src/github_release_context.py --repo owner/name --from v1.2.3 --to v1.3.0 --out-dir ./out
```

Outputs
-------

- The script writes two artifacts into the `out/` directory by default:
	- `release_context_<owner>_<repo>_<from>_to_<to>.md`
	- `release_context_<owner>_<repo>_<from>_to_<to>.json`

- The `out/` directory is ignored by git (see `.gitignore`).

Notes
-----

- If you don't have `tqdm` installed, the script falls back to a simple progress indicator.
- To include PR file lists (slower), pass `--include-pr-files`.
