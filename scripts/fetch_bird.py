"""Download BIRD Mini-Dev (SQLite) into data/bird/ and verify it.

Sources:
- Questions + official baseline prompts: Hugging Face ``birdsql/bird_mini_dev`` (the canonical,
  2026-updated annotations), pinned to one commit and checked against BIRD's own sha256.
- SQLite databases: the BIRD Mini-Dev "complete package" (Google Drive link from the official
  README; the Aliyun mirror serves the same databases but downloads ~100x slower).

Licence: CC BY-SA 4.0 (BIRD). The data is not committed to this repo; this script recreates it.

Usage:  uv run python scripts/fetch_bird.py            (downloads)
        uv run python scripts/fetch_bird.py --zip PATH (use an already-downloaded minidev.zip)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

from data_agent import paths

HF_REPO = "birdsql/bird_mini_dev"
HF_REVISION = "5b256b5a9c17478f06ed6912612e9a14d17065c8"  # 2026-10-27, "Mini-Dev 1024"
HF_FILES = {
    # remote path -> (local name, expected sha256 or None)
    "data/mini_dev_sqlite.jsonl": (
        paths.BIRD_QUESTIONS.name,
        "926af2a0d1b47e5d0d15a7aa8a34d4721007f4550a881555b6a89496b54a2606",  # from BIRD manifest
    ),
    "data/manifest.json": ("manifest.json", None),
    "baseline_inference/mini_dev_sqlite.jsonl": (paths.BIRD_BASELINE_PROMPTS.name, None),
    "baseline_inference/prompt_template.txt": ("prompt_template.txt", None),
}
DB_ZIP_URL = (
    "https://drive.usercontent.google.com/download"
    "?id=13VLWIwpw5E3d5DUkMvzw7hvHE67a4XkG&export=download&confirm=t"
)
DB_MANIFEST = paths.EVAL_DIR / "data_manifest.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    print(f"downloading {url}\n  -> {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(resp, out, length=1 << 20)
    tmp.replace(dest)


def fetch_hf() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for remote, (local, expected) in HF_FILES.items():
        dest = paths.BIRD_DIR / local
        url = f"https://huggingface.co/datasets/{HF_REPO}/resolve/{HF_REVISION}/{remote}"
        download(url, dest)
        digest = sha256(dest)
        if expected and digest != expected:
            sys.exit(f"sha256 mismatch for {remote}: {digest} != {expected}")
        hashes[local] = digest
    return hashes


def extract_databases(zip_path: Path) -> dict[str, str]:
    """Extract only the .sqlite files (no scripts, no __MACOSX) into data/bird/dev_databases."""
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.startswith("__MACOSX/") or not name.endswith(".sqlite"):
                continue
            parts = Path(name).parts
            if "dev_databases" not in parts:
                continue
            db_id = Path(name).stem
            if not db_id.replace("_", "").isalnum():
                sys.exit(f"unexpected database name in zip: {name!r}")
            dest = paths.BIRD_DATABASES / db_id / f"{db_id}.sqlite"
            dest.parent.mkdir(parents=True, exist_ok=True)
            print(f"extracting {db_id} ({info.file_size / 1e6:.0f} MB)")
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, length=1 << 20)
            hashes[db_id] = sha256(dest)
    return dict(sorted(hashes.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, help="existing minidev.zip (skips the download)")
    args = parser.parse_args()

    hf_hashes = fetch_hf()
    zip_path: Path = args.zip or (paths.RAW_DIR / "minidev.zip")
    if args.zip is None and not zip_path.exists():
        download(DB_ZIP_URL, zip_path)
    db_hashes = extract_databases(zip_path)

    questions = [json.loads(x) for x in paths.BIRD_QUESTIONS.read_text("utf-8").splitlines() if x]
    missing = sorted({q["db_id"] for q in questions} - set(db_hashes))
    if missing:
        sys.exit(f"databases missing from zip: {missing}")

    manifest = {
        "hf_repo": HF_REPO,
        "hf_revision": HF_REVISION,
        "hf_files_sha256": hf_hashes,
        "database_zip_url": DB_ZIP_URL,
        "database_zip_sha256": sha256(zip_path),
        "databases_sha256": db_hashes,
        "licence": "CC BY-SA 4.0 (BIRD benchmark)",
    }
    if DB_MANIFEST.exists():
        old = json.loads(DB_MANIFEST.read_text("utf-8"))
        if old != manifest:
            print(f"WARNING: data differs from the committed {DB_MANIFEST.name}; see git diff")
    DB_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"ok: {len(questions)} questions, {len(db_hashes)} databases. Manifest: {DB_MANIFEST}")


if __name__ == "__main__":
    main()
