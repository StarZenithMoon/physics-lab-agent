"""Export a reviewable source archive without local environments/data/results."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from labcore.project import ROOT

FILES = {"README.md", "AGENTS.md", "LICENSE", ".gitignore", ".gitattributes", ".env.example",
         "requirements.txt", "requirements-mc.txt", "requirements-verified.txt"}
FOLDERS = {".agents", ".opencode", ".github", "labcore", "docs", "references", "templates",
           "examples", "evaluation", "rsi", "tests", "tools"}
SUFFIXES = {".py", ".md", ".json", ".csv", ".txt", ".yaml", ".yml"}
PUBLIC_DATA = {"demo-pendulum", "public"}


def source_files(root=ROOT):
    root = Path(root).resolve()
    for directory, subdirs, names in os.walk(root, followlinks=False):
        current = Path(directory)
        relative_dir = current.relative_to(root)
        if current == root:
            subdirs[:] = [d for d in subdirs if d in FOLDERS or d == "data"]
        elif relative_dir == Path("data"):
            subdirs[:] = [d for d in subdirs if d in PUBLIC_DATA]
        # Prune before traversing, including links/junctions to non-project files.
        subdirs[:] = sorted(d for d in subdirs if d not in {"runs", "__pycache__", ".git", "node_modules", ".venv"}
                            and not (current/d).is_symlink() and not (current/d).is_junction())
        for name in sorted(names):
            path = current/name
            if relative_dir == Path('.opencode') and name in {'opencode.json','opencode.jsonc'}:
                continue  # Loaded local provider settings are never part of the public source export.
            if path.is_symlink() or not path.is_file():
                continue
            if current == root:
                if name in FILES:
                    yield path
            elif relative_dir != Path("data") and path.suffix in SUFFIXES and not name.endswith(".local.json"):
                yield path


def package(output, root=ROOT):
    root, output = Path(root).resolve(), Path(output).resolve()
    files = list(source_files(root))
    if not files:
        raise ValueError("no project sources found")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"format": 1, "scope": "source, reviewed public reference cases, simulated demo, and tests; no private data/runs",
                "files": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    # Refuse overwrite: archives are reviewable snapshots, not a silent replacement.
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, "physics-lab-agent/"+path.relative_to(root).as_posix())
        archive.writestr("physics-lab-agent/SOURCE-MANIFEST.json",
                         json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    print(f"source_archive: {output} ({len(files)} source files)")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="new .zip path, normally under dist/")
    args = parser.parse_args()
    package(args.out)
