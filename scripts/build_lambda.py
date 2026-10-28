"""Builds infra/build/lambda.zip: our code + locked dependencies for Lambda (arm64, py3.14).

Reproducible: files are added in sorted order with a fixed timestamp, so the same
inputs always give the same zip (and the same Terraform source_code_hash).
Run from the repo root:  .venv/bin/python scripts/build_lambda.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "infra" / "build" / "lambda.zip"
FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        subprocess.run(  # noqa: S603
            [
                sys.executable, "-m", "pip", "install", "--quiet", "--no-compile",
                "--target", str(stage),
                "--platform", "manylinux2014_aarch64", "--only-binary=:all:",
                "--implementation", "cp", "--python-version", "3.14",
                "-r", str(ROOT / "backend" / "requirements.lock.txt"),
            ],
            check=True,
        )  # fmt: skip
        shutil.copytree(
            ROOT / "backend" / "src" / "docqa", stage / "docqa",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )  # fmt: skip
        files = sorted(p for p in stage.rglob("*") if p.is_file())
        with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                info = zipfile.ZipInfo(f.relative_to(stage).as_posix(), FIXED_TIME)
                info.external_attr = 0o644 << 16
                zf.writestr(info, f.read_bytes(), zipfile.ZIP_DEFLATED)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1_000_000:.1f} MB, {len(files)} files)")


if __name__ == "__main__":
    main()
