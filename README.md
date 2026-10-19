# Q4 – File Organization System

A responsive Flask dashboard for safely organizing files **uploaded to the web app**. Each browser session gets a separate server-side workspace. The app never scans the computer, reads local folders, or executes uploaded content. Folder placement is based on extensions; recommendations are explicit local rules, not machine-learning output. No API key is needed.

## Run online (Replit or GitHub Codespaces)

1. Open this project folder in Replit or a GitHub Codespace.
2. In the project terminal, install dependencies:

   ```sh
   python -m pip install -r requirements.txt
   ```

3. Start the Flask server:

   ```sh
   python app.py
   ```

   The app listens on `0.0.0.0` and uses the `PORT` environment variable when supplied (otherwise port `5000`).
4. Open the platform's web preview for port `5000` (or the value shown in `PORT`). In Codespaces, open the **Ports** panel and click the forwarded port URL. In Replit, use **Open in a new tab** for the detected web server.

For a stable hosted session secret, set `FLASK_SECRET_KEY` in the platform's secrets/environment panel. Do not commit secret values.

## Run tests

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Tests use Flask's in-process test client and temporary upload directories. They cover startup, extension categorization, duplicate names, path traversal sanitization, preview without movement, confirmation, ZIP downloads, CSRF, activity logging, and session isolation.

## How to use

- Upload one or more files with the file picker or drag and drop. The upload request limit is 32 MB; at most 50 files can be submitted per request.
- Check the file table, local recommendations, and folder preview.
- Select **Organize files** and confirm in the browser dialog. Moves occur only inside that session's private workspace.
- Download the organized files as a ZIP, or use **Clear session** to remove that session's uploaded and organized files.

Supported extension groups include Documents (PDF, Word, text), Images, Spreadsheets (CSV and Excel), Archives, Audio, Videos, and Others. Unknown extensions remain downloadable under **Others**. Filenames are sanitized on upload; duplicate names receive `_1`, `_2`, etc. Activity is logged per session without recording file contents.

## Project files

- `app.py` — Flask routes, categorization, isolated workspaces, confirmation, and ZIP generation.
- `templates/index.html` — accessible dashboard layout.
- `static/css/style.css` — responsive blue-and-white styling.
- `static/js/script.js` — upload, drag and drop, search, preview, confirmation, and downloads.
- `requirements.txt` — Flask dependency.
- `tests/` — automated CLI and web app tests using temporary folders.
- `organize_files.py` — retained preview-first command-line organizer from the existing project.
- `sample_data/` — fictional synthetic sample files; the web app does not access them automatically.

The app uses the Flask development server, which is suitable for a class project and platform preview. For a public production deployment, run behind the hosting platform's production WSGI server and configure HTTPS and a persistent secret.
