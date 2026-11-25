"""AI-generated BOM drafting: provider dispatch, clean-disabled-by-default, offline.

The one property that actually matters: with AI_PROVIDER unset (the default), the
feature must say so plainly rather than raising an opaque error or silently doing
nothing -- see pipeline.ai_bom.is_enabled(). Everything downstream of generation
(parsing, pricing) is exercised by test_export_bom.py already; this file only
covers the generation step itself, with both backends' network calls faked.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ai_bom reads settings via config.get_settings(), which needs no DB -- no temp
# sqlite setup required for this file, unlike the pipeline tests.
os.environ.setdefault("BUILDHUB_DB_URL", "sqlite:///:memory:")

from buildhub.config import get_settings  # noqa: E402
from buildhub.pipeline import ai_bom  # noqa: E402

FAILS: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  (got {got!r}, want {want!r})"))
    if not ok:
        FAILS.append(label)


def check_true(label, got):
    check(label, bool(got), True)


def _set_env(**kw):
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


# --------------------------------------------------------------------- disabled


def test_disabled_by_default():
    print("\ndisabled by default")
    _set_env(AI_PROVIDER=None)
    check("is_enabled() is False with no provider set", ai_bom.is_enabled(), False)
    try:
        ai_bom.generate_parts_list("esp32 weather station")
        check("raises when disabled", False, True)
    except RuntimeError as e:
        check_true("error message mentions AI_PROVIDER", "AI_PROVIDER" in str(e))


def test_unknown_provider():
    print("\nunknown provider name")
    _set_env(AI_PROVIDER="not-a-real-provider")
    check("is_enabled() is True for any non-none value", ai_bom.is_enabled(), True)
    try:
        ai_bom.generate_parts_list("esp32 weather station")
        check("raises for an unknown provider", False, True)
    except RuntimeError as e:
        check_true("error names the bad provider", "not-a-real-provider" in str(e))
    _set_env(AI_PROVIDER=None)


# --------------------------------------------------------------- line cleaning


def test_clean_lines():
    print("\ncleaning model output")
    raw = (
        "```\n"
        "- 1 x ESP32-WROOM-32E dev board\n"
        "* 1 x BME280 breakout\n"
        "2. 1 x 128x64 OLED display\n"
        "\n"
        "4 x M3x10 socket head screw\n"
        "```\n"
    )
    cleaned = ai_bom._clean_lines(raw)
    lines = cleaned.splitlines()
    check("fences stripped, blank lines dropped", len(lines), 4)
    check("leading bullet stripped", lines[0], "1 x ESP32-WROOM-32E dev board")
    check("leading asterisk bullet stripped", lines[1], "1 x BME280 breakout")
    check("leading numbering stripped", lines[2], "1 x 128x64 OLED display")
    check("plain line untouched", lines[3], "4 x M3x10 socket head screw")


# -------------------------------------------------------------------- anthropic


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, text):
        self._text = text
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeMessage(self._text)


class _FakeAnthropicClient:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def test_anthropic_backend():
    print("\nanthropic backend")
    _set_env(AI_PROVIDER="anthropic", AI_API_KEY=None)
    try:
        ai_bom.generate_parts_list("esp32 weather station")
        check("raises without AI_API_KEY", False, True)
    except RuntimeError as e:
        check_true("error mentions AI_API_KEY", "AI_API_KEY" in str(e))

    _set_env(AI_API_KEY="test-key")
    import anthropic

    fake_client = _FakeAnthropicClient("2 x ESP32-WROOM-32E dev board\n1 x BME280 breakout")
    real_anthropic_cls = anthropic.Anthropic
    anthropic.Anthropic = lambda api_key: fake_client
    try:
        out = ai_bom.generate_parts_list("esp32 weather station with a bme280")
        check("two lines returned", len(out.splitlines()), 2)
        check_true("model called with the system prompt",
                   fake_client.messages.last_kwargs["system"] == ai_bom.SYSTEM_PROMPT)
    finally:
        anthropic.Anthropic = real_anthropic_cls
    _set_env(AI_PROVIDER=None, AI_API_KEY=None)


# ------------------------------------------------------------------------ ollama


class _FakeOllamaResponse:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        pass

    def json(self):
        return {"response": self._text}


def test_ollama_backend():
    print("\nollama backend")
    # OLLAMA_BASE_URL defaults to a placeholder host (config.py), not empty -- so the
    # "unset" case is an explicit empty string, not just removing the env var.
    _set_env(AI_PROVIDER="ollama", OLLAMA_BASE_URL="")
    try:
        ai_bom.generate_parts_list("esp32 weather station")
        check("raises with an empty OLLAMA_BASE_URL", False, True)
    except RuntimeError as e:
        check_true("error mentions OLLAMA_BASE_URL", "OLLAMA_BASE_URL" in str(e))

    _set_env(OLLAMA_BASE_URL="http://ollama.local:11434")
    import httpx

    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        return _FakeOllamaResponse("1 x NEMA17 stepper motor\n1 x A4988 stepper driver")

    real_post = httpx.post
    httpx.post = fake_post
    try:
        out = ai_bom.generate_parts_list("a simple CNC axis")
        check("two lines returned", len(out.splitlines()), 2)
        check("posts to the configured base url",
              calls["url"], "http://ollama.local:11434/api/generate")
        check("model name passed through", calls["json"]["model"], "llama3.1")
    finally:
        httpx.post = real_post
    _set_env(AI_PROVIDER=None, OLLAMA_BASE_URL=None)


# ------------------------------------------------------------------ openai-compatible


class _FakeOpenAIResponse:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._text}}]}


def test_openai_backend():
    print("\nopenai-compatible backend (OmniRoute)")
    _set_env(AI_PROVIDER="openai", LLM_BASE_URL="", LLM_MODEL=None, LLM_API_KEY=None)
    try:
        ai_bom.generate_parts_list("esp32 weather station")
        check("raises with an empty LLM_BASE_URL", False, True)
    except RuntimeError as e:
        check_true("error mentions LLM_BASE_URL", "LLM_BASE_URL" in str(e))

    _set_env(LLM_BASE_URL="http://gateway.local:20128/v1/", LLM_API_KEY="test-key")
    import httpx

    calls = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers
        calls["json"] = json
        return _FakeOpenAIResponse("2 x M3 heat-set insert\n1 x 608 bearing")

    real_post = httpx.post
    httpx.post = fake_post
    try:
        out = ai_bom.generate_parts_list("a spool holder")
        check("two lines returned", len(out.splitlines()), 2)
        check("posts to chat/completions under the base url",
              calls["url"], "http://gateway.local:20128/v1/chat/completions")
        check("default model is the apps-default combo", calls["json"]["model"], "apps-default")
        check("bearer key sent", calls["headers"].get("Authorization"), "Bearer test-key")
    finally:
        httpx.post = real_post
    _set_env(AI_PROVIDER=None, LLM_BASE_URL=None, LLM_API_KEY=None)


def run() -> int:
    test_disabled_by_default()
    test_unknown_provider()
    test_clean_lines()
    test_anthropic_backend()
    test_ollama_backend()
    test_openai_backend()

    print()
    print("=" * 62)
    if FAILS:
        print(f"{len(FAILS)} FAILURES")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("AI BOM generation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
