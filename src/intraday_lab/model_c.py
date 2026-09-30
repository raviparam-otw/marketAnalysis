from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import request


@dataclass(frozen=True)
class ModelCAdvisor:
    """OpenAI-compatible adapter for the future LLM trading model.

    Model C is deliberately shadow-only until an endpoint and model are configured.
    It cannot submit broker orders. The execution engine remains A/B-only for action day.
    """

    base_url: str
    model: str
    api_key: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip() and self.model.strip())

    def status(self) -> dict:
        return {
            "name": "C",
            "role": "SHADOW",
            "label": "LLM Adaptive",
            "configured": self.configured,
            "execution_enabled": False,
            "model": self.model or None,
            "base_url": self.base_url or None,
            "message": (
                "Shadow advisor ready; execution intentionally disabled."
                if self.configured
                else "Set MODEL_C_LLM_BASE_URL and MODEL_C_LLM_MODEL to connect an open-source endpoint."
            ),
        }

    def advise(self, payload: dict, timeout_seconds: int = 20) -> dict:
        if not self.configured:
            raise RuntimeError("Model C LLM endpoint is not configured.")

        endpoint = self.base_url.rstrip("/") + "/chat/completions"
        prompt = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a shadow intraday trading analyst. Return JSON only with "
                        "decision (BUY/HOLD), confidence 0-1, rationale, risks, and invalidation. "
                        "You do not place orders."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, separators=(",", ":")),
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = request.Request(
            endpoint,
            data=json.dumps(prompt).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"decision": "HOLD", "confidence": 0.0, "rationale": text, "parse_error": True}
