"""Prepare an allowlisted static-site bundle. This command never deploys it."""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from . import ROOT

PUBLIC_FILES = ("index.html", "styles.css", "app.mjs", "chart.mjs", ".nojekyll", "data/ili.json")


def stage_site(output, source=ROOT / "site"):
    output, source = Path(output), Path(source)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty, preventing stale private files from being published")
    for name in PUBLIC_FILES:
        if not (source / name).is_file():
            raise ValueError(f"Missing public file: {name}; build the database first")
    payload = json.loads((source / "data/ili.json").read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not payload.get("reports"):
        raise ValueError("Invalid or empty public data")
    for name in PUBLIC_FILES:
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage only public HTML/CSS/JS/JSON; do not deploy.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is None:
        (ROOT / "tmp").mkdir(exist_ok=True)
        args.output = Path(tempfile.mkdtemp(prefix="public-site-", dir=ROOT / "tmp"))
    print(stage_site(args.output).resolve())
