from __future__ import annotations

import argparse
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


LOG = logging.getLogger("finr1-local-server")


class FinR1Runtime:
    def __init__(self, model_path: str, model_id: str) -> None:
        self.model_path = model_path
        self.model_id = model_id
        self.model = None
        self.tokenizer = None

    def load(self) -> None:
        if self.model is not None:
            return

        from mlx_lm import load

        LOG.info("Loading Fin-R1 from %s", self.model_path)
        self.model, self.tokenizer = load(self.model_path)
        LOG.info("Fin-R1 loaded")

    def chat(self, body: dict[str, Any]) -> str:
        self.load()

        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a non-empty array")

        max_tokens = int(body.get("max_tokens", 900) or 900)
        max_tokens = max(32, min(max_tokens, 1200))

        temperature = float(body.get("temperature", 0.1) or 0.0)
        top_p = float(body.get("top_p", 0.8) or 0.0)

        prompt = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
        )

        sampler = make_sampler(
            temp=max(0.0, temperature),
            top_p=max(0.0, min(top_p, 1.0)),
        )

        LOG.info(
            "Generating response: messages=%s max_tokens=%s temperature=%s top_p=%s",
            len(messages),
            max_tokens,
            temperature,
            top_p,
        )

        result = generate(
            self.model,
            self.tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            sampler=sampler,
            verbose=False,
        )
        return str(result)


class Handler(BaseHTTPRequestHandler):
    runtime: FinR1Runtime

    server_version = "MarketAnalysisFinR1/1.0"

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/v1/models":
            self._json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": self.runtime.model_id,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "marketAnalysis-local-mlx",
                        }
                    ],
                },
            )
            return

        if self.path in {"/health", "/v1/health"}:
            self._json(
                200,
                {
                    "status": "ok",
                    "model": self.runtime.model_id,
                    "runtime": "marketAnalysis-local-mlx",
                },
            )
            return

        self._json(404, {"error": {"message": "Not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": {"message": "Not found"}})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            requested_model = str(body.get("model") or "")
            if requested_model and requested_model not in {
                self.runtime.model_id,
                self.runtime.model_path,
                "Fin-R1",
            }:
                LOG.warning(
                    "Request model id %s differs from loaded model %s; using loaded model",
                    requested_model,
                    self.runtime.model_id,
                )

            text = self.runtime.chat(body)
            self._json(
                200,
                {
                    "id": f"chatcmpl-local-{int(time.time() * 1000)}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": self.runtime.model_id,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": text,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                },
            )
        except Exception as exc:
            LOG.exception("Fin-R1 generation failed")
            self._json(
                500,
                {
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                },
            )

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Single-threaded local OpenAI-compatible server for Fin-R1 on MLX."
    )
    parser.add_argument("--model", default=".models/Fin-R1-4bit")
    parser.add_argument("--model-id", default="SUFE-AIFLM-Lab/Fin-R1")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    model_path = Path(args.model)
    if not model_path.exists():
        raise SystemExit(f"Fin-R1 model path does not exist: {model_path}")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    Handler.runtime = FinR1Runtime(str(model_path), args.model_id)
    server = HTTPServer((args.host, args.port), Handler)

    LOG.info(
        "Starting single-threaded Fin-R1 server at http://%s:%s/v1",
        args.host,
        args.port,
    )
    LOG.info("Model ID: %s", args.model_id)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Stopping Fin-R1 server")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
