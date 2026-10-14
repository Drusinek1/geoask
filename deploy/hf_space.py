"""Create or update the GeoAsk Hugging Face Space (Docker SDK).

Requires HF_TOKEN (write scope) and ANTHROPIC_API_KEY in the environment.
The Anthropic key is stored as a Space secret, never written to the repo.

  python deploy/hf_space.py --space <user>/geoask
"""
from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["app.py", "requirements.txt", "Dockerfile", "geoask", "etl", "static", "data/raw",
           "eval/cases.json", "eval/run_eval.py"]  # data/raw includes the C-CAP tile and census CSV

SPACE_FRONTMATTER = """---
title: GeoAsk
emoji: 🗺️
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Ask a map a question, get SQL you can check
---

"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, help="e.g. Drusinek1/geoask")
    ap.add_argument("--private", action="store_true")
    a = ap.parse_args()

    token = os.environ.get("HF_TOKEN")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not token or not api_key:
        raise SystemExit("set HF_TOKEN and ANTHROPIC_API_KEY first")

    api = HfApi(token=token)
    api.create_repo(a.space, repo_type="space", space_sdk="docker", private=a.private, exist_ok=True)
    api.add_space_secret(a.space, "ANTHROPIC_API_KEY", api_key)
    api.add_space_variable(a.space, "GEOASK_PER_IP_PER_HOUR", os.environ.get("GEOASK_PER_IP_PER_HOUR", "10"))
    api.add_space_variable(a.space, "GEOASK_PER_DAY", os.environ.get("GEOASK_PER_DAY", "150"))

    # Stage a copy so the Space README gets its YAML frontmatter without touching the repo's.
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        import shutil
        for item in INCLUDE:
            src, dst = ROOT / item, stage / item
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme = re.sub(r"^!\[GeoAsk screenshot\]\(docs/screenshot\.png\)\n?", "", readme, flags=re.M)
        (stage / "README.md").write_text(SPACE_FRONTMATTER + readme, encoding="utf-8")

        api.upload_folder(repo_id=a.space, repo_type="space", folder_path=str(stage),
                          commit_message="Deploy GeoAsk", delete_patterns=["*"])
    print(f"https://huggingface.co/spaces/{a.space}")


if __name__ == "__main__":
    main()
