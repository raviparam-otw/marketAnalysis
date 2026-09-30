#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x ".venv/bin/mlx_lm.server" ]]; then
  echo "mlx-lm is not installed. Run ./scripts/setup_model_c_macos.sh first."
  exit 1
fi

if [[ ! -f ".models/Fin-R1-4bit/config.json" ]]; then
  echo "Fin-R1 model is missing. Run ./scripts/setup_model_c_macos.sh first."
  exit 1
fi

source .venv/bin/activate
echo "Starting Fin-R1 at http://127.0.0.1:8080/v1 ..."
exec mlx_lm.server --model .models/Fin-R1-4bit
