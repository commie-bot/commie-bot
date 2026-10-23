"""Pluggable LLM backends that return a JSON object matching a schema.

Three providers:
- cerebras (default): Cerebras Inference, through its OpenAI-compatible API.
- anthropic: Claude through the Anthropic API.
- openai: any OpenAI-compatible chat completions API (OpenAI, Ollama,
  OpenRouter, Groq, Together, vLLM, LM Studio, llama.cpp server, and others).
  Set LLM_BASE_URL to point at a non-OpenAI server.
"""
import json
import re

from config import config


class LLMError(Exception):
    pass


def parse_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating fences and reasoning tags."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise LLMError(f"No JSON object in model output: {text[:200]!r}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"Invalid JSON from model: {e}") from e


def _anthropic(system: str, user: str, schema: dict) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=config.LLM_API_KEY or None, base_url=config.LLM_BASE_URL or None)
    response = client.beta.messages.create(
        model=config.LLM_MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "refusal":
        raise LLMError(f"Model declined the request: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise LLMError("Model output was truncated")
    return parse_json(next(b.text for b in response.content if b.type == "text"))


def _openai(system: str, user: str, schema: dict) -> dict:
    import openai

    client = openai.OpenAI(
        api_key=config.LLM_API_KEY or "not-needed",  # local servers ignore the key
        base_url=config.LLM_BASE_URL or None,
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    formats = [
        {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}},
        {"type": "json_object"},  # for servers without json_schema support
    ]
    for i, fmt in enumerate(formats):
        try:
            response = client.chat.completions.create(
                model=config.LLM_MODEL, messages=messages, response_format=fmt, max_tokens=16000
            )
            break
        except openai.BadRequestError:
            if i == len(formats) - 1:
                raise
            messages[0]["content"] = f"{system}\n\nRespond with only a JSON object matching this schema:\n{json.dumps(schema)}"
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise LLMError("Model output was truncated")
    return parse_json(choice.message.content or "")


PROVIDERS = {"cerebras": _openai, "anthropic": _anthropic, "openai": _openai}


def complete_json(system: str, user: str, schema: dict) -> dict:
    provider = PROVIDERS.get(config.LLM_PROVIDER)
    if not provider:
        raise LLMError(f"Unknown LLM_PROVIDER {config.LLM_PROVIDER!r}; use one of {', '.join(PROVIDERS)}")
    return provider(system, user, schema)
