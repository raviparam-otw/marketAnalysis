#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE_URL="http://127.0.0.1:8080/v1"
RUNTIME_DIR=".runtime"
LOG_FILE="$RUNTIME_DIR/finr1-server.log"
PID_FILE="$RUNTIME_DIR/finr1-server.pid"

say() {
  printf '\n==> %s\n' "$1"
}

fail() {
  printf '\n[FAIL] %s\n' "$1" >&2
  exit 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "This automated Model C setup is for macOS."
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  fail "Fin-R1 MLX setup requires Apple Silicon (arm64)."
fi

if [[ ! -x ".venv/bin/python" ]]; then
  fail "Missing .venv. Create it with: python3 -m venv .venv"
fi

source .venv/bin/activate
mkdir -p "$RUNTIME_DIR"

NEEDS_SETUP=false
if ! python -c 'import mlx_lm' >/dev/null 2>&1; then
  NEEDS_SETUP=true
fi
if [[ ! -f ".models/Fin-R1-4bit/config.json" ]]; then
  NEEDS_SETUP=true
fi
if [[ ! -d ".models/Kronos/.git" ]]; then
  NEEDS_SETUP=true
fi

if [[ "$NEEDS_SETUP" == "true" ]]; then
  say "Installing/downloading Model C dependencies and models"
  bash scripts/setup_model_c_macos.sh
else
  say "Model C local files already present; skipping heavy download"
fi

if [[ ! -f ".env" ]]; then
  say "Creating .env from .env.example"
  cp .env.example .env
fi

server_json() {
  curl -fsS --max-time 3 "$BASE_URL/models" 2>/dev/null || true
}

server_ready() {
  [[ -n "$(server_json)" ]]
}

server_owned_by_us() {
  local payload
  payload="$(server_json)"
  [[ -n "$payload" ]] || return 1
  printf '%s' "$payload" | python -c '
import json,sys
try:
    data=json.load(sys.stdin).get("data", [])
    print("yes" if data and data[0].get("owned_by")=="marketAnalysis-local-mlx" else "no")
except Exception:
    print("no")
' | grep -q '^yes$'
}

server_is_finr1() {
  local payload
  payload="$(server_json)"
  [[ -n "$payload" ]] || return 1
  printf '%s' "$payload" | python -c '
import json,sys
try:
    data=json.load(sys.stdin).get("data", [])
    model=(data[0].get("id","") if data else "").lower()
    print("yes" if "fin-r1" in model or "fin_r1" in model else "no")
except Exception:
    print("no")
' | grep -q '^yes$'
}

if server_ready && ! server_owned_by_us; then
  if server_is_finr1; then
    say "Replacing older mlx_lm.server Fin-R1 process with reliable local server"
    OLD_PIDS="$(lsof -tiTCP:8080 -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$OLD_PIDS" ]]; then
      kill $OLD_PIDS 2>/dev/null || true
      for _ in $(seq 1 20); do
        if ! server_ready; then
          break
        fi
        sleep 0.5
      done
    fi
  else
    fail "Port 8080 is already used by another service. Stop that service and rerun."
  fi
fi

if server_owned_by_us; then
  say "Reliable Fin-R1 server is already running"
else
  say "Starting reliable Fin-R1 server in background"
  : >"$LOG_FILE"
  nohup bash scripts/start_finr1_macos.sh >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"

  READY=false
  for _ in $(seq 1 90); do
    if server_owned_by_us; then
      READY=true
      break
    fi
    sleep 2
  done

  if [[ "$READY" != "true" ]]; then
    echo "Last Fin-R1 server log lines:"
    tail -n 80 "$LOG_FILE" || true
    fail "Fin-R1 server did not become ready within 180 seconds."
  fi
fi

say "Detecting Fin-R1 model ID from the running server"
MODEL_ID="$(
  server_json |
    python -c 'import json,sys; data=json.load(sys.stdin).get("data", []); print(data[0]["id"] if data else "")'
)"

if [[ -z "$MODEL_ID" ]]; then
  fail "Fin-R1 server returned no model ID."
fi

printf 'Detected model ID: %s\n' "$MODEL_ID"

say "Updating only Model C settings in .env (existing Alpaca credentials are preserved)"
MODEL_C_BASE_URL="$BASE_URL" MODEL_C_MODEL_ID="$MODEL_ID" python - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

path = Path(".env")
text = path.read_text(encoding="utf-8") if path.exists() else ""
lines = text.splitlines()

updates = {
    "MODEL_C_ENABLED": "true",
    "MODEL_C_EXECUTION_ENABLED": "true",
    "MODEL_C_REQUIRE_FULL_STACK": "false",
    "MODEL_C_LLM_BASE_URL": os.environ["MODEL_C_BASE_URL"],
    "MODEL_C_LLM_MODEL": os.environ["MODEL_C_MODEL_ID"],
    "MODEL_C_LLM_API_KEY": "",
    "MODEL_C_LLM_TIMEOUT_SECONDS": "240",
    "MODEL_C_DECISION_INTERVAL_SECONDS": "60",
    "MODEL_C_SHORTLIST_SIZE": "5",
    "MODEL_C_MIN_CONFIDENCE": "0.68",
    "MODEL_C_FINBERT_ENABLED": "true",
    "MODEL_C_FINBERT_MODEL": "ProsusAI/finbert",
    "MODEL_C_KRONOS_ENABLED": "true",
    "MODEL_C_KRONOS_REPO_PATH": ".models/Kronos",
    "MODEL_C_KRONOS_MODEL": "NeoQuasar/Kronos-small",
    "MODEL_C_KRONOS_TOKENIZER": "NeoQuasar/Kronos-Tokenizer-base",
    "MODEL_C_KRONOS_DEVICE": "cpu",
    "MODEL_C_KRONOS_LOOKBACK": "120",
    "MODEL_C_KRONOS_PRED_LEN": "5",
}

seen: set[str] = set()
out: list[str] = []

for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        out.append(line)
        continue

    key = line.split("=", 1)[0].strip()
    if key in updates:
        if key not in seen:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        continue

    out.append(line)

missing = [key for key in updates if key not in seen]
if missing:
    if out and out[-1].strip():
        out.append("")
    out.append("# Model C auto-configured local stack")
    out.extend(f"{key}={updates[key]}" for key in missing)

path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY

say "Verifying configuration loaded by the app"
PYTHONPATH=src python - <<'PY'
from intraday_lab.config import Settings

s = Settings()
print("BASE URL:", s.model_c_llm_base_url)
print("MODEL:", s.model_c_llm_model)
print("FULL STACK REQUIRED:", s.model_c_require_full_stack)
print("KRONOS DEVICE:", s.model_c_kronos_device)

if not s.model_c_llm_base_url:
    raise SystemExit("MODEL_C_LLM_BASE_URL was not loaded from .env")
if not s.model_c_llm_model:
    raise SystemExit("MODEL_C_LLM_MODEL was not loaded from .env")
PY

say "Running FinBERT + Kronos + Fin-R1 full-stack check"
if ! PYTHONPATH=src python scripts/check_model_c_stack.py; then
  echo
  echo "Last Fin-R1 server log lines:"
  tail -n 120 "$LOG_FILE" 2>/dev/null || true
  fail "Model C full-stack check failed."
fi

say "Running live A/C sample test"
if ! PYTHONPATH=src python sample_test.py --live-llm; then
  echo
  echo "Last Fin-R1 server log lines:"
  tail -n 120 "$LOG_FILE" 2>/dev/null || true
  fail "Live A/C sample test failed."
fi

cat <<EOF

============================================================
MODEL C AUTOMATION COMPLETE
============================================================
Fin-R1 server : $BASE_URL
Fin-R1 model  : $MODEL_ID
Server log    : $LOG_FILE

FinBERT       : tested
Kronos        : tested
Fin-R1        : tested
A/C sample    : tested

No Alpaca orders were submitted by these validation commands.
============================================================
EOF
