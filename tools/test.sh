#!/usr/bin/env bash
# Build the test image and run pytest inside it with the repo mounted at /work.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
docker build -q -t sketch-to-cad-dev "$ROOT" >/dev/null
docker run --rm -u 1000 -v "$ROOT":/work -w /work sketch-to-cad-dev pytest "$@"
