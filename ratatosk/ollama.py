"""Ollama inference — lazy network imports only inside call paths."""
from __future__ import annotations

import json
import os

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
TIMEOUT_GENERATE = 120
TIMEOUT_LIST = 5


def is_available() -> bool:
    try:
        import requests

        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=TIMEOUT_LIST)
        return response.status_code == 200
    except Exception:
        return False


def chat(messages: list[dict], model: str | None = None) -> str:
    import requests

    model = model or OLLAMA_MODEL
    response = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": model, "messages": messages, "stream": False},
        timeout=TIMEOUT_GENERATE,
    )
    response.raise_for_status()
    return response.json().get("message", {}).get("content", "")


def generate(prompt: str, model: str | None = None, system: str | None = None) -> str:
    import requests

    model = model or OLLAMA_MODEL
    payload: dict = {"model": model, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    response = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json=payload,
        timeout=TIMEOUT_GENERATE,
    )
    response.raise_for_status()
    return response.json().get("response", "")
