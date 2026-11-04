"""Plain HTTP calls to model APIs (stdlib only).

Two wire formats cover almost everything:
  kind = "openai"     OpenAI, OpenRouter, Groq, Together, DeepSeek, Mistral, Gemini (OpenAI endpoint),
                      Ollama, LM Studio, vLLM, LiteLLM proxy...
  kind = "anthropic"  Anthropic, plus Anthropic-compatible gateways (OpenRouter, Kimi, GLM, DeepSeek...)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from conclave.config import Provider


class ProviderError(Exception):
    pass


def _post(url: str, headers: dict[str, str], body: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise ProviderError(f"HTTP {e.code} from {url}: {detail}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"cannot reach {url}: {e.reason}") from e


def chat(p: Provider, model: str, system: str, user: str, max_tokens: int = 4000, timeout: int = 600) -> str:
    if not model:
        raise ProviderError(f"provider {p.name!r}: no model set")
    key = p.api_key
    if p.api_key_env and not key:
        raise ProviderError(f"provider {p.name!r}: ${p.api_key_env} is not set")
    base = p.resolved_base_url()

    if p.kind == "anthropic":
        headers = {"anthropic-version": "2023-06-01", **p.headers}
        if key:
            headers["x-api-key"] = key
        d = _post(f"{base}/v1/messages", headers, {
            "model": model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}],
        }, timeout)
        return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text").strip()

    headers = dict(p.headers)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    d = _post(f"{base}/chat/completions", headers, {
        "model": model, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }, timeout)
    try:
        return (d["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError) as e:
        raise ProviderError(f"unexpected response from {base}: {json.dumps(d)[:300]}") from e


def provider_env(p: Provider | None) -> dict[str, str]:
    """Environment that points an agent CLI at this provider (Claude Code, Codex, aider... read these)."""
    if p is None:
        return {}
    env: dict[str, str] = {}
    key = p.api_key
    if p.kind == "anthropic":
        if key:
            env["ANTHROPIC_API_KEY"] = key
        if p.base_url:
            env["ANTHROPIC_BASE_URL"] = p.base_url.rstrip("/")
            if key:
                env["ANTHROPIC_AUTH_TOKEN"] = key
    else:
        if key:
            env["OPENAI_API_KEY"] = key
        if p.base_url:
            env["OPENAI_BASE_URL"] = p.base_url.rstrip("/")
            env["OPENAI_API_BASE"] = p.base_url.rstrip("/")  # aider / litellm spelling
    return env
