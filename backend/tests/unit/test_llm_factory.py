"""
Unit tests for provider-agnostic LLM factory (src/agents/llm_factory.py).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.agents.llm_factory import (
    LLMNotAvailableError,
    _detect_provider,
    _get_model_name,
    get_llm,
)


# ── _detect_provider ──────────────────────────────────────────────────────────


def test_detect_ollama() -> None:
    """OLLAMA_BASE_URL set → ollama."""
    with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://127.0.0.1:11434"}, clear=True):
        assert _detect_provider() == "ollama"


def test_detect_anthropic() -> None:
    """ANTHROPIC_API_KEY set without OLLAMA → anthropic."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
        assert _detect_provider() == "anthropic"


def test_detect_openai() -> None:
    """OPENAI_API_KEY set without others → openai."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
        assert _detect_provider() == "openai"


def test_detect_deepseek() -> None:
    """DEEPSEEK_API_KEY set without others → deepseek."""
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True):
        assert _detect_provider() == "deepseek"


def test_detect_none() -> None:
    """No provider configured → none."""
    with patch.dict(os.environ, {}, clear=True):
        assert _detect_provider() == "none"


def test_detect_ollama_priority() -> None:
    """Ollama takes priority even when cloud keys are present."""
    with patch.dict(os.environ, {
        "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "DEEPSEEK_API_KEY": "sk-test",
    }, clear=True):
        assert _detect_provider() == "ollama"


# ── _get_model_name ───────────────────────────────────────────────────────────


def test_model_name_ollama_causal() -> None:
    """Causal reasoning on ollama maps to the large model (default qwen3.5:122b)."""
    with patch.dict("src.agents.llm_factory._OLLAMA_MODELS", {
        "causal_reasoning": "test-model",
        "fast_classification": "test-fast",
        "document_extraction": "test-doc",
        "code_generation": "test-code",
    }):
        name = _get_model_name("ollama", "causal_reasoning")
        assert name == "test-model"


def test_model_name_ollama_fast() -> None:
    """Fast classification gets correct model name."""
    with patch.dict("src.agents.llm_factory._OLLAMA_MODELS", {
        "causal_reasoning": "qwen3.5:122b",
        "fast_classification": "test-fast",
        "document_extraction": "nemotron",
        "code_generation": "deepseek",
    }):
        name = _get_model_name("ollama", "fast_classification")
        assert name == "test-fast"


def test_model_name_anthropic() -> None:
    """Anthropic models resolve to claude variants."""
    with patch.dict(os.environ, {}, clear=True):
        name = _get_model_name("anthropic", "causal_reasoning")
        assert "claude" in name.lower()


def test_model_name_unknown_provider() -> None:
    """Unknown provider returns 'unknown'."""
    assert _get_model_name("nonexistent", "causal_reasoning") == "unknown"


# ── get_llm ───────────────────────────────────────────────────────────────────


def test_get_llm_no_provider() -> None:
    """get_llm raises LLMNotAvailableError when nothing is configured."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(LLMNotAvailableError):
            get_llm()
