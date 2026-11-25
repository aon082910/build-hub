"""AI-generated parts lists from a plain-language build description.

Deliberately narrow: the model's only job is turning "ESP32 weather station with
BME280 and an OLED" into a plain parts list, one line per part as ``qty x item`` --
the exact shape ``parse_bom()`` (pipeline/export.py) already parses. Everything
downstream (search, matching, landed-cost costing, links) is the existing BOM
pipeline, unmodified. This module never touches pricing or the catalog itself.

Off by default (``AI_PROVIDER=none``), matching the app's existing convention for
optional providers (see pipeline/translate.py) -- callers should check
``is_enabled()`` and show a plain "AI is disabled" message rather than erroring.
"""

from __future__ import annotations

import logging
import re

from ..config import get_settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a parts-sourcing assistant for electronics and 3D-printing \
(RepRap-style) builds. Given a plain-language description of something someone wants \
to build, output the bill of materials as a plain list, ONE PART PER LINE, in the \
exact form:

<qty> x <specific, buyable part description>

Rules:
- Be specific and buyable: real component types, common values/sizes, connector or \
socket types where it matters ("NEMA17 stepper motor, 42x42mm, 1.8deg" not "motor").
- Include fasteners, wiring, and consumables the build actually needs, not just the \
headline components.
- Quantities must be realistic for one build (e.g. one MCU board, four M3x10 screws).
- Do not include tools someone already owns (screwdrivers, soldering iron) unless the \
description is specifically about acquiring tools.
- No explanations, no headers, no markdown, no numbering -- only the part lines, one \
per line, in the "qty x description" form shown above.
"""


def is_enabled() -> bool:
    return (get_settings().ai_provider or "none").lower() != "none"


def generate_parts_list(description: str) -> str:
    """Return newline-separated ``qty x part`` lines for a build description.

    Raises RuntimeError if AI is disabled or misconfigured -- callers are expected
    to check is_enabled() first for a clean UX, but this still fails loudly rather
    than silently returning nothing if it's called anyway.
    """
    settings = get_settings()
    provider = (settings.ai_provider or "none").lower()

    if provider == "none":
        raise RuntimeError("AI_PROVIDER is not set (none). Set AI_PROVIDER=anthropic or ollama.")
    if provider == "anthropic":
        raw = _generate_anthropic(description, settings)
    elif provider == "ollama":
        raw = _generate_ollama(description, settings)
    elif provider == "openai":
        raw = _generate_openai(description, settings)
    else:
        raise RuntimeError(f"Unknown AI_PROVIDER: {provider!r} (expected anthropic, ollama or openai)")

    return _clean_lines(raw)


def _generate_openai(description: str, settings) -> str:
    """OpenAI-compatible endpoint (OmniRoute and similar)."""
    import httpx

    base_url = (settings.llm_base_url or "").rstrip("/")
    if not base_url:
        raise RuntimeError("AI_PROVIDER=openai but LLM_BASE_URL is unset")
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    resp = httpx.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json={
            "model": settings.llm_model,
            "max_tokens": 2000,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": description},
            ],
        },
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"] or ""


def _generate_anthropic(description: str, settings) -> str:
    import anthropic

    if not settings.ai_api_key:
        raise RuntimeError("AI_PROVIDER=anthropic but AI_API_KEY is unset")

    client = anthropic.Anthropic(api_key=settings.ai_api_key)
    msg = client.messages.create(
        model=settings.ai_model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": description}],
    )
    return "".join(block.text for block in msg.content if block.type == "text")


def _generate_ollama(description: str, settings) -> str:
    import httpx

    base_url = (settings.ollama_base_url or "").rstrip("/")
    if not base_url:
        raise RuntimeError("AI_PROVIDER=ollama but OLLAMA_BASE_URL is unset")

    resp = httpx.post(
        f"{base_url}/api/generate",
        json={
            "model": settings.ollama_model,
            "system": SYSTEM_PROMPT,
            "prompt": description,
            "stream": False,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def _clean_lines(raw: str) -> str:
    """Strip markdown fences/bullets/numbering the model added despite instructions."""
    lines = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        line = re.sub(r"^[-*•]\s*", "", line)
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if line:
            lines.append(line)
    return "\n".join(lines)
