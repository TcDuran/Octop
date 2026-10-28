#!/usr/bin/env bash
# Build a full wheel package including the latest dashboard frontend.
# Run from repo root: bash scripts/wheel_build.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "[wheel_build] Building and verifying branded dashboard..."
make build-frontend

echo "[wheel_build] Building wheel + sdist..."
python3 -m pip install --quiet build
rm -rf dist/*
python3 -m build --outdir dist .

python3 brand/verify-octop-brand.py \
    --artifact "$(find "$REPO_ROOT/dist" -maxdepth 1 -type f -name 'octop-*.whl' -print -quit)"

echo "[wheel_build] Done. Artifacts in: $REPO_ROOT/dist/"
ls -la dist/
