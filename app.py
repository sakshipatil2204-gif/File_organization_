"""Flask web dashboard for safe, session-isolated file organization."""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file, session
from werkzeug.utils import secure_filename

CATEGORIES = {
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"},
    "Spreadsheets": {".csv", ".xls", ".xlsx", ".ods"},
    "Archives": {".zip", ".7z", ".rar", ".tar", ".gz"},
    "Audio": {".mp3", ".wav", ".m4a", ".flac", ".ogg"},
    "Videos": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
}
MAX_FILES_PER_UPLOAD = 50
MAX_FILENAME_LENGTH = 180
EXTENSION_RE = re.compile(r"^\.[A-Za-z0-9]{1,12}$")


def category_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    for category, extensions in CATEGORIES.items():
        if suffix in extensions:
            return category
    return "Others"


def normalized_filename(filename: str) -> str:
    """Return a safe display-only naming recommendation, not a rename action."""
    path = Path(filename)
    words = re.findall(r"[A-Za-z0-9]+", path.stem)
    if not words:
        return filename
    stem = "-".join(word.lower() for word in words)
    return (stem[:120] or "file") + path.suffix.lower()


def file_recommendation(filename: str) -> dict:
    category = category_for(filename)
    proposed_name = normalized_filename(filename)
    if category == "Others":
        reason = "No listed extension rule matched; kept in Others for review."
    else:
        reason = f"The {Path(filename).suffix.lower()} extension maps to {category}."
    if proposed_name != filename:
        naming = f"Consider {proposed_name} for consistent lowercase, hyphenated naming."
    else:
        naming = "Name is already clear; add a project or date prefix if that helps retrieval."
    return {"category": category, "reason": reason, "suggested_name": proposed_name, "tip": naming}


def unique_path(path: Path) -> Path:
    if not path.exists() and not path.is_symlink():
        return path
    number = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{number}{path.suffix}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        number += 1


def copy_without_overwrite(source: Path, destination: Path) -> Path:
    """Copy to an exclusively created name and return the name used."""
    while True:
        target = unique_path(destination)
        created = False
        try:
            with source.open("rb") as input_stream, target.open("xb") as output_stream:
                created = True
                shutil.copyfileobj(input_stream, output_stream)
            return target
        except FileExistsError:
            continue
        except Exception:
            if created:
                target.unlink(missing_ok=True)
            raise


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=32 * 1024 * 1024,
        UPLOAD_ROOT=Path(app.instance_path) / "uploads",
        MAX_FILES_PER_UPLOAD=MAX_FILES_PER_UPLOAD,
    )
    if test_config:
        app.config.update(test_config)
    Path(app.config["UPLOAD_ROOT"]).mkdir(parents=True, exist_ok=True)

    def session_key() -> str:
        value = session.get("workspace_id")
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            value = secrets.token_hex(32)
            session["workspace_id"] = value
        return value

    def workspace() -> Path:
        root = Path(app.config["UPLOAD_ROOT"]).resolve()
        path = (root / session_key()).resolve()
        if path.parent != root:
            abort(400, description="Invalid workspace.")
        (path / "incoming").mkdir(parents=True, exist_ok=True)
        (path / "organized").mkdir(parents=True, exist_ok=True)
        return path

    def csrf_token() -> str:
        token = session.get("csrf_token")
        if not isinstance(token, str):
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    def require_csrf() -> None:
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            body = {}
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or body.get("csrf_token")
        if not supplied or not secrets.compare_digest(str(supplied), csrf_token()):
            abort(403, description="The request token is missing or invalid. Refresh the page and try again.")

    def record_event(action: str, details: str) -> None:
        root = workspace()
        event = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": action,
            "details": details[:300],
        }
        with (root / "activity.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_activity() -> list[dict]:
        path = workspace() / "activity.jsonl"
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines()[-50:]:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(events))

    def inventory() -> list[dict]:
        root = workspace()
        results = []
        incoming = root / "incoming"
        organized = root / "organized"
        for path in incoming.iterdir():
            if path.is_file() and not path.is_symlink():
                rec = file_recommendation(path.name)
                results.append({"name": path.name, "size": path.stat().st_size, "category": rec["category"],
                                "reason": rec["reason"], "suggested_name": rec["suggested_name"],
                                "tip": rec["tip"], "status": "Ready"})
        for folder in organized.iterdir():
            if folder.is_dir() and not folder.is_symlink():
                for path in folder.iterdir():
                    if path.is_file() and not path.is_symlink():
                        rec = file_recommendation(path.name)
                        results.append({"name": path.name, "size": path.stat().st_size, "category": folder.name,
                                        "reason": rec["reason"], "suggested_name": rec["suggested_name"],
                                        "tip": rec["tip"], "status": "Organized"})
        return sorted(results, key=lambda item: item["name"].casefold())

    def state_payload() -> dict:
        files = inventory()
        pending = [item for item in files if item["status"] == "Ready"]
        grouped: dict[str, list[str]] = {}
        for item in pending:
            grouped.setdefault(item["category"], []).append(item["name"])
        return {
            "files": files,
            "pending_count": len(pending),
            "organized_count": sum(item["status"] == "Organized" for item in files),
            "categories": {name: len(group) for name, group in grouped.items()},
            "preview": grouped,
            "activity": read_activity(),
        }

    @app.get("/")
    def index():
        csrf_token()
        workspace()
        return render_template("index.html", csrf_token=session["csrf_token"])

    @app.get("/api/state")
    def api_state():
        return jsonify(state_payload())

    @app.post("/api/upload")
    def api_upload():
        require_csrf()
        uploads = request.files.getlist("files")
        if not uploads or all(not item.filename for item in uploads):
            return jsonify(error="Choose at least one file to upload."), 400
        if len(uploads) > app.config["MAX_FILES_PER_UPLOAD"]:
            return jsonify(error=f"Upload at most {app.config['MAX_FILES_PER_UPLOAD']} files at a time."), 400
        incoming = workspace() / "incoming"
        prepared = []
        for item in uploads:
            if not item.filename:
                continue
            safe_name = secure_filename(item.filename)[:MAX_FILENAME_LENGTH]
            if not safe_name or safe_name in {".", ".."}:
                return jsonify(error="A filename could not be safely processed."), 400
            suffix = Path(safe_name).suffix
            if suffix and not EXTENSION_RE.fullmatch(suffix):
                return jsonify(error=f"Unsupported or invalid extension in {safe_name}."), 400
            prepared.append((item, safe_name))
        saved = []
        for item, safe_name in prepared:
            destination = unique_path(incoming / safe_name)
            item.save(destination)
            saved.append(destination.name)
            record_event("upload", f"Received {destination.name} ({destination.stat().st_size} bytes).")
        return jsonify(message=f"Uploaded {len(saved)} file(s).", saved=saved, state=state_payload()), 201

    @app.post("/api/organize")
    def api_organize():
        require_csrf()
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            body = {}
        if body.get("confirmed") is not True:
            return jsonify(error="Confirm the preview before organizing."), 400
        root = workspace()
        incoming = root / "incoming"
        organized = root / "organized"
        moved = []
        for source in sorted(incoming.iterdir(), key=lambda item: item.name.casefold()):
            if not source.is_file() or source.is_symlink():
                continue
            category = category_for(source.name)
            destination_dir = organized / category
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = copy_without_overwrite(source, destination_dir / source.name)
            source.unlink()
            moved.append({"name": destination.name, "category": category})
            record_event("organized", f"Moved {destination.name} into {category}.")
        return jsonify(message=f"Organized {len(moved)} file(s).", moved=moved, state=state_payload())

    @app.get("/api/download.zip")
    def api_download():
        root = workspace() / "organized"
        files = []
        for folder in root.iterdir():
            if folder.is_dir() and not folder.is_symlink():
                for path in folder.iterdir():
                    if path.is_file() and not path.is_symlink():
                        files.append(path)
        if not files:
            return jsonify(error="Organize at least one file before downloading a ZIP."), 400
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(files, key=lambda item: item.as_posix().casefold()):
                archive.write(path, arcname=f"{path.parent.name}/{path.name}")
        buffer.seek(0)
        return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name="organized_files.zip")

    @app.post("/api/reset")
    def api_reset():
        require_csrf()
        root = workspace()
        for name in ("incoming", "organized"):
            target = root / name
            if target.is_dir() and not target.is_symlink() and target.parent == root:
                shutil.rmtree(target)
        (root / "incoming").mkdir()
        (root / "organized").mkdir()
        record_event("reset", "Cleared this session's uploaded and organized files.")
        return jsonify(message="This session was cleared.", state=state_payload())

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(error="Upload is too large. The request limit is 32 MB."), 413

    @app.errorhandler(400)
    def bad_request(error):
        if request.path.startswith("/api/"):
            return jsonify(error=getattr(error, "description", "Invalid request.")), 400
        return error

    @app.errorhandler(403)
    def forbidden(error):
        return jsonify(error=getattr(error, "description", "Forbidden.")), 403

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
