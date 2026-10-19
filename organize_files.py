"""Preview-first file organizer with optional OpenAI filename suggestions."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = PROJECT_DIR / "sample_data"
DEFAULT_LOG = PROJECT_DIR / "logs" / "file_operations.jsonl"
TYPE_FOLDERS = {
    ".pdf": "Documents/PDF", ".doc": "Documents/Word", ".docx": "Documents/Word",
    ".txt": "Documents/Text", ".rtf": "Documents/Text", ".odt": "Documents/Word",
    ".jpg": "Images/Photos", ".jpeg": "Images/Photos", ".png": "Images/Graphics",
    ".gif": "Images/Graphics", ".bmp": "Images/Graphics", ".webp": "Images/Graphics",
    ".csv": "Data/Spreadsheets", ".xlsx": "Data/Spreadsheets", ".xls": "Data/Spreadsheets",
    ".json": "Data/Structured", ".xml": "Data/Structured",
    ".py": "Code/Python", ".js": "Code/JavaScript", ".html": "Code/Web",
    ".zip": "Archives", ".mp3": "Audio", ".mp4": "Video",
}


def target_folder(path: Path) -> str:
    """Return the relative category path, based on the file extension."""
    ext = path.suffix.lower()
    if ext in TYPE_FOLDERS:
        return TYPE_FOLDERS[ext]
    mime, _ = mimetypes.guess_type(path.name)
    if mime:
        return mime.split("/", 1)[0].title() + "/Other"
    return "Other/Unknown"


def suggest_categories(files: list[Path]) -> list[dict]:
    """Make useful local suggestions from names and types; no API key needed."""
    by_group: dict[str, list[str]] = defaultdict(list)
    for path in files:
        by_group[target_folder(path)].append(path.name)
    suggestions = []
    for folder, names in sorted(by_group.items()):
        sample = ", ".join(names[:3])
        suggestions.append({
            "folder": folder,
            "file_count": len(names),
            "examples": names[:3],
            "suggestion": (
                f"Group {folder.split('/')[0].lower()} files together. "
                f"Consider YYYY-MM-DD_topic_version.ext for dated or versioned files. "
                f"Examples: {sample}."
            ),
        })
    return suggestions


def ask_openai(files: list[Path], model: str) -> str:
    """Request optional suggestions from OpenAI. Only filenames are sent."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set. Use --ai local or configure the key.")
    body = {
        "model": model,
        "input": "Suggest a concise folder taxonomy and filename convention for these filenames. Return plain text only; do not claim to inspect file contents.\n"
        + json.dumps([p.name for p in files]),
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        data = json.loads(response.read())
    output = [part.get("text", "") for item in data.get("output", [])
              for part in item.get("content", []) if part.get("type") == "output_text"]
    return "\n".join(output).strip() or "(No suggestion returned.)"


def list_source_files(source: Path) -> list[Path]:
    """Return only direct child files; never recurse into the selected folder."""
    return sorted((p for p in source.iterdir() if p.is_file() and not p.is_symlink()),
                  key=lambda p: p.name.casefold())


def plan_moves(source: Path, files: list[Path]) -> list[tuple[Path, Path]]:
    return [(path, source / target_folder(path) / path.name) for path in files]


def unique_destination(dest: Path) -> Path:
    """Choose a free destination without modifying the filesystem."""
    if not dest.exists() and not dest.is_symlink():
        return dest
    n = 1
    while True:
        candidate = dest.with_name(f"{dest.stem}_{n}{dest.suffix}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        n += 1


def append_log(log_path: Path, action: str, src: Path, dest: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "source": str(src),
        "destination": str(dest),
    }
    with log_path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")


def move_without_overwrite(src: Path, dest: Path) -> None:
    """Move a file while atomically refusing an existing destination."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Hard-link creation is atomic and fails if dest already exists.
        os.link(src, dest)
    except FileExistsError:
        raise
    except OSError:
        # Cross-volume/unsupported-link fallback reserves dest exclusively.
        with src.open("rb") as incoming, dest.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        src.unlink()
    else:
        src.unlink()


def apply_moves(moves: list[tuple[Path, Path]], log_path: Path) -> list[tuple[Path, Path]]:
    completed = []
    for src, planned in moves:
        dest = unique_destination(planned)
        # A collision between planning and execution is handled by recalculation.
        while True:
            try:
                move_without_overwrite(src, dest)
                break
            except FileExistsError:
                dest = unique_destination(planned)
        append_log(log_path, "move", src, dest)
        completed.append((src, dest))
    return completed


def safe_source(raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"Source must not be a symbolic link: {candidate}")
    source = candidate.resolve()
    if not source.is_dir():
        raise ValueError(f"Source must be an existing, ordinary directory: {source}")
    # Never allow drive roots or common whole-computer/system roots.
    forbidden = {Path(source.anchor).resolve(), Path.home().resolve()}
    for env_name in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(env_name)
        if value:
            forbidden.add(Path(value).resolve())
    if source in forbidden:
        raise ValueError("Refusing to organize a drive, home, or system root. Choose a specific folder.")
    return source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely classify and organize files in one selected folder.")
    parser.add_argument("source", nargs="?", default=str(DEFAULT_SOURCE), help="One folder to organize (default: project's sample_data)")
    parser.add_argument("--apply", action="store_true", help="Show the plan and then ask for typed confirmation before moving")
    parser.add_argument("--ai", choices=("local", "openai"), default="local", help="Suggestions (local works without a key)")
    parser.add_argument("--model", default="gpt-4.1-mini", help="OpenAI model for --ai openai")
    parser.add_argument("--log", default=str(DEFAULT_LOG), help="Operation log path")
    args = parser.parse_args(argv)
    try:
        source = safe_source(args.source)
    except ValueError as exc:
        parser.error(str(exc))
    files = list_source_files(source)
    if not files:
        print(f"No files found directly inside {source}")
        return 0

    moves = plan_moves(source, files)
    print(f"\nPREVIEW: {source}")
    for src, dest in moves:
        print(f"  {src.name}  ->  {dest.relative_to(source).as_posix()}")
    print("\nFolder and naming suggestions:")
    if args.ai == "openai" and os.environ.get("OPENAI_API_KEY"):
        try:
            print(ask_openai(files, args.model))
        except Exception as exc:
            print(f"OpenAI suggestions unavailable ({exc}); using local suggestions.")
            for item in suggest_categories(files):
                print(f"  {item['folder']} ({item['file_count']}): {item['suggestion']}")
    else:
        if args.ai == "openai":
            print("  OPENAI_API_KEY is not configured; using local suggestions.")
        for item in suggest_categories(files):
            print(f"  {item['folder']} ({item['file_count']}): {item['suggestion']}")

    if not args.apply:
        print("\nPreview only; no files were changed. Re-run with --apply to review and confirm moves.")
        return 0
    print("\nReview the preview above. Type ORGANIZE to move these files, or anything else to cancel:")
    try:
        confirmed = input("> ").strip() == "ORGANIZE"
    except EOFError:
        confirmed = False
    if not confirmed:
        print("Cancelled; no files were moved.")
        return 1
    completed = apply_moves(moves, Path(args.log).expanduser().resolve())
    print(f"\nMoved {len(completed)} file(s). Log: {Path(args.log).expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

