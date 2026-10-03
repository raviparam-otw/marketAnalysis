from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

MODEL_A_V2 = {
    "MODEL_A_CONFIRMATION_BARS": "2",
    "MODEL_A_MAX_EXTENSION_FROM_OR_PCT": "0.03",
    "MODEL_A_MAX_EXTENSION_FROM_VWAP_PCT": "0.03",
    "MODEL_A_REENTRY_COOLDOWN_MINUTES": "10",
    "MODEL_A_REENTRY_MIN_BARS": "5",
    "MODEL_A_MIN_POSITION_PCT": "0.02",
    "MODEL_A_STRUCTURE_CONFIRM_BARS": "2",
    "MODEL_A_STRUCTURE_BREAK_PCT": "0.001",
    "MODEL_A_PROFIT_LOCK_TRIGGER_PCT": "0.0075",
    "MODEL_A_PROFIT_LOCK_PCT": "0.003",
}


def main() -> int:
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH}. Create it from .env.example first.")

    lines = ENV_PATH.read_text().splitlines()
    remaining = dict(MODEL_A_V2)
    updated: list[str] = []

    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            updated.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            updated.append(f"{key}={remaining.pop(key)}")
        else:
            updated.append(line)

    if remaining:
        updated.append("")
        updated.append("# Model A v2 structural trade-management profile")
        updated.extend(f"{key}={value}" for key, value in remaining.items())

    ENV_PATH.write_text("\n".join(updated).rstrip() + "\n")
    print("Updated Model A v2 settings in .env:")
    for key, value in MODEL_A_V2.items():
        print(f"  {key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
