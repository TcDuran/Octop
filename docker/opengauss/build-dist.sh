#!/usr/bin/env bash
# Build both required wheels; install them together, without an unpublished index.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if (( $# )); then OUT="$1"; else OUT="$ROOT/dist/opengauss"; fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
cd "$ROOT"
echo "[build-dist] Building and verifying branded dashboard..."
make build-frontend
uv build --wheel --out-dir "$OUT" .
uv build --wheel --out-dir "$OUT" vendor/octop-memory/octop_memory-1.0.2+opengauss.2.tar.gz
OCTOP_WHEEL="$(find "$OUT" -maxdepth 1 -type f -name 'octop-*.whl' -print | head -n 1)"
python3 brand/verify-octop-brand.py --artifact "$OCTOP_WHEEL"
printf 'Install the two generated wheels together: pip install <octop wheel> <octop_memory wheel>\n'
