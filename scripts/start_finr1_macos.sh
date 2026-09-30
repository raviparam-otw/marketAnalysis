#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

source .venv/bin/activate

if ! python -c 'import mlx_lm' >/dev/null 2>&1; then
  echo "mlx-lm is not installed. Run: bash scripts/setup_model_c_macos.sh"
  exit 1
fi

if [[ ! -f ".models/Fin-R1-4bit/config.json" ]]; then
  echo "Fin-R1 model is missing. Run ./scripts/setup_model_c_macos.sh first."
  exit 1
fi

echo "Starting reliable single-threaded Fin-R1 at http://127.0.0.1:8080/v1 ..."
exec python scripts/finr1_local_server.py \
  --model .models/Fin-R1-4bit \
  --model-id SUFE-AIFLM-Lab/Fin-R1 \
  --host 127.0.0.1 \
  --port 8080
