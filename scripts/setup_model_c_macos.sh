#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This setup script is for macOS/Apple Silicon."
  exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Fin-R1 MLX setup requires Apple Silicon (arm64)."
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv. Create it first: python3 -m venv .venv"
  exit 1
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-model-c-macos.txt

mkdir -p .models

if [[ ! -d ".models/Kronos/.git" ]]; then
  git clone --depth 1 https://github.com/shiyu-coder/Kronos.git .models/Kronos
else
  git -C .models/Kronos pull --ff-only
fi

FINR1_DIR=".models/Fin-R1-4bit"
if [[ ! -f "$FINR1_DIR/config.json" ]]; then
  rm -rf "$FINR1_DIR"
  mlx_lm.convert \
    --hf-path SUFE-AIFLM-Lab/Fin-R1 \
    --mlx-path "$FINR1_DIR" \
    --quantize \
    --q-bits 4
else
  echo "Fin-R1 MLX model already exists at $FINR1_DIR"
fi

python - <<'PY'
from huggingface_hub import snapshot_download

for model_id in (
    "ProsusAI/finbert",
    "NeoQuasar/Kronos-small",
    "NeoQuasar/Kronos-Tokenizer-base",
):
    print(f"Preloading {model_id}...")
    snapshot_download(repo_id=model_id)
print("Model C supporting weights cached.")
PY

cat <<'EOF'

Model C models are installed.

Add/update these values in .env:

MODEL_C_ENABLED=true
MODEL_C_EXECUTION_ENABLED=true
MODEL_C_REQUIRE_FULL_STACK=true
MODEL_C_LLM_BASE_URL=http://127.0.0.1:8080/v1
MODEL_C_LLM_MODEL=.models/Fin-R1-4bit
MODEL_C_LLM_API_KEY=
MODEL_C_LLM_TIMEOUT_SECONDS=60

MODEL_C_FINBERT_ENABLED=true
MODEL_C_FINBERT_MODEL=ProsusAI/finbert

MODEL_C_KRONOS_ENABLED=true
MODEL_C_KRONOS_REPO_PATH=.models/Kronos
MODEL_C_KRONOS_MODEL=NeoQuasar/Kronos-small
MODEL_C_KRONOS_TOKENIZER=NeoQuasar/Kronos-Tokenizer-base
MODEL_C_KRONOS_LOOKBACK=120
MODEL_C_KRONOS_PRED_LEN=5

Start Fin-R1 in another terminal with:
  ./scripts/start_finr1_macos.sh

Then verify all three finance components with:
  PYTHONPATH=src python scripts/check_model_c_stack.py
EOF
