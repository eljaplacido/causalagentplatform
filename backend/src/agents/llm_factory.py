"""
Provider-agnostic LLM client factory.

Per AGENTS.md §7, agents specify capability requirements, not model names.
This factory selects the provider based on environment configuration and
capability routing rules.

Provider selection order:
    1. OLLAMA_BASE_URL (set)     → Ollama (local)   — preferred on a local GPU host
    2. ANTHROPIC_API_KEY (set)   → Anthropic Claude — primary cloud
    3. OPENAI_API_KEY (set)      → OpenAI GPT       — fallback cloud
    4. DEEPSEEK_API_KEY (set)    → DeepSeek          — fallback cloud

Capability routing (AGENTS.md §7):
    - causal_reasoning   → larger models (ollama: qwen3.5 / nemotron)
    - fast_classification → smaller models (ollama: phi4 / llama3.2)
    - code_generation    → frontier models

Usage::

    from src.agents.llm_factory import get_llm

    llm = get_llm(capability="causal_reasoning")
    response = await llm.ainvoke("Explain the causal chain...")
"""

from __future__ import annotations

import os
from typing import Any, Literal

import structlog

from src.secret_source import read_secret

log = structlog.get_logger(__name__)

#: The API-key secret of each cloud provider. Each is read through
#: ``src/secret_source.py``, so ``<NAME>_FILE`` naming a mounted file works as well
#: as ``<NAME>`` in the environment.
_API_KEY_SECRET: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def _api_key(provider: str) -> str | None:
    name = _API_KEY_SECRET.get(provider)
    return read_secret(name) if name else None

# ── Capability type ───────────────────────────────────────────────────────────

Capability = Literal[
    "causal_reasoning",
    "fast_classification",
    "document_extraction",
    "code_generation",
    "planning",
]

# ── Provider detection ────────────────────────────────────────────────────────


def _detect_provider() -> str:
    """Detect the active LLM provider from environment variables."""
    # Local-first preference: Ollama first
    if os.getenv("OLLAMA_BASE_URL"):
        return "ollama"
    for provider in ("anthropic", "openai", "deepseek"):
        if _api_key(provider):
            return provider
    return "none"


# ── Model mapping by capability ───────────────────────────────────────────────


_OLLAMA_MODELS: dict[Capability, str] = {
    "causal_reasoning": os.getenv(
        "OLLAMA_CAUSAL_MODEL", os.getenv("OLLAMA_MODEL", "qwen3.5:122b")
    ),
    "fast_classification": os.getenv(
        "OLLAMA_FAST_MODEL", os.getenv("OLLAMA_MODEL", "phi4:14b")
    ),
    "document_extraction": os.getenv(
        "OLLAMA_DOC_MODEL", os.getenv("OLLAMA_MODEL", "nemotron-super:120b")
    ),
    "code_generation": os.getenv(
        "OLLAMA_CODE_MODEL", os.getenv("OLLAMA_MODEL", "deepseek-r1:70b")
    ),
}

_ANTHROPIC_MODELS: dict[Capability, str] = {
    "causal_reasoning": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
    "fast_classification": os.getenv("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5-20250413"),
    "document_extraction": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
    "code_generation": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
}

_DEEPSEEK_MODELS: dict[Capability, str] = {
    "causal_reasoning": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    "fast_classification": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    "document_extraction": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    "code_generation": os.getenv("DEEPSEEK_MODEL", "deepseek-reasoner"),
}

_OPENAI_MODELS: dict[Capability, str] = {
    "causal_reasoning": os.getenv("OPENAI_MODEL", "gpt-4o"),
    "fast_classification": os.getenv("OPENAI_FAST_MODEL", "gpt-4o-mini"),
    "document_extraction": os.getenv("OPENAI_MODEL", "gpt-4o"),
    "code_generation": os.getenv("OPENAI_MODEL", "gpt-4o"),
}


# ── Model name resolution ─────────────────────────────────────────────────────


#: The environment variable that overrides each capability's Ollama model. Read at
#: call time as well as at import, so a model chosen after the process started (a
#: test, a notebook) is the one used.
_OLLAMA_MODEL_ENV: dict[Capability, str] = {
    "causal_reasoning": "OLLAMA_CAUSAL_MODEL",
    "fast_classification": "OLLAMA_FAST_MODEL",
    "document_extraction": "OLLAMA_DOC_MODEL",
    "code_generation": "OLLAMA_CODE_MODEL",
}


def _get_model_name(provider: str, capability: Capability) -> str:
    """Resolve the model name for a given provider and capability."""
    mapping: dict[Capability, str] | None = None
    if provider == "ollama":
        if capability in _OLLAMA_MODELS:
            env = _OLLAMA_MODEL_ENV.get(capability)
            override = (os.getenv(env) if env else None) or os.getenv("OLLAMA_MODEL")
            if override:
                return override
        mapping = _OLLAMA_MODELS
    elif provider == "anthropic":
        mapping = _ANTHROPIC_MODELS
    elif provider == "deepseek":
        mapping = _DEEPSEEK_MODELS
    elif provider == "openai":
        mapping = _OPENAI_MODELS

    if mapping is None:
        return "unknown"

    return mapping.get(capability, "unknown")


# ── LLM client construction ───────────────────────────────────────────────────


class LLMNotAvailableError(RuntimeError):
    """Raised when no LLM provider is configured."""


def llm_configured() -> bool:
    """True when some provider is configured. Callers with a deterministic fallback check this
    first, so that an unconfigured platform takes the fallback without a failed client build."""
    return _detect_provider() != "none"


def get_llm(
    *,
    capability: Capability = "causal_reasoning",
    temperature: float = 0.2,
    session_id: str | None = None,
    agent_id: str | None = None,
    prompt_template_id: str | None = None,
    prompt_template_hash: str | None = None,
) -> Any:
    """
    Return a LangChain-compatible chat model for the given capability.

    Provider is auto-detected from environment variables. Falls back to
    the first available provider if the preferred one is unavailable.

    When session_id and agent_id are provided, the returned LLM is wrapped
    to auto-capture LLMInvocation metadata after every call.

    Args:
        capability: Reasoning capability needed (defaults to causal_reasoning)
        temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative)
        session_id: Optional session ID for provenance tracking
        agent_id: Optional agent ID for provenance tracking
        prompt_template_id: The versioned template the prompt was rendered from
            (``prompts/<skill>.yaml@<version>``), recorded on every captured invocation
        prompt_template_hash: sha256 of that template file, recorded likewise

    Returns:
        A LangChain BaseChatModel instance.

    Raises:
        LLMNotAvailableError: If no provider is configured.

    Examples:
        llm = get_llm(capability="fast_classification")
        llm = get_llm(capability="causal_reasoning", temperature=0.0)
        llm = get_llm(capability="causal_reasoning",
                       session_id="sess-123", agent_id="discovery-agent")
    """
    provider = _detect_provider()
    model_name = _get_model_name(provider, capability)

    log.info(
        "llm_factory.constructing",
        provider=provider,
        capability=capability,
        model=model_name,
    )

    try:
        llm = _build_llm(provider, model_name, capability, temperature)
    except Exception as exc:
        log.warning(
            "llm_factory.primary_failed",
            provider=provider,
            error=str(exc),
        )
        # Fall back to next available provider
        llm, provider = _build_fallback_llm(capability, temperature)
        model_name = _get_model_name(provider, capability)

    # ── Auto-capture LLM invocation metadata for provenance tracking ─────────
    if session_id and agent_id:
        llm = _wrap_with_invocation_capture(
            llm,
            session_id,
            agent_id,
            provider,
            model_name,
            capability,
            temperature=temperature,
            prompt_template_id=prompt_template_id,
            prompt_template_hash=prompt_template_hash,
        )

    return llm


def _build_llm(
    provider: str,
    model_name: str,
    capability: Capability,
    temperature: float,
) -> Any:
    """Build LLM client for a specific provider."""
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        return ChatOllama(
            model=model_name,
            base_url=base_url,
            temperature=temperature,
            num_predict=int(os.getenv("OLLAMA_MAX_TOKENS", "4096")),
        )

    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        key = _api_key("anthropic")
        return ChatAnthropic(
            model=model_name,
            temperature=temperature,
            max_tokens=int(os.getenv("ANTHROPIC_MAX_TOKENS", "4096")),
            **({"api_key": key} if key else {}),
        )

    elif provider == "openai":
        from langchain_openai import ChatOpenAI

        key = _api_key("openai")
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "4096")),
            **({"api_key": key} if key else {}),
        )

    elif provider == "deepseek":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            max_tokens=int(os.getenv("DEEPSEEK_MAX_TOKENS", "4096")),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            api_key=_api_key("deepseek") or "",
        )

    raise LLMNotAvailableError(f"Unknown provider: {provider}")


def _build_fallback_llm(capability: Capability, temperature: float) -> tuple[Any, str]:
    """Try fallback providers in order of preference: ollama → anthropic → openai → deepseek."""
    fallback_order = ["ollama", "anthropic", "openai", "deepseek"]
    active_provider = _detect_provider()

    for provider in fallback_order:
        if provider == active_provider:
            continue  # Already tried primary
        try:
            if provider == "ollama" and os.getenv("OLLAMA_BASE_URL"):
                pass  # Only use ollama if configured
            elif _api_key(provider):
                pass
            else:
                continue

            model_name = _get_model_name(provider, capability)
            log.info(
                "llm_factory.fallback",
                provider=provider,
                capability=capability,
                model=model_name,
            )
            return _build_llm(provider, model_name, capability, temperature), provider
        except Exception as exc:
            log.warning(
                "llm_factory.fallback_failed",
                provider=provider,
                error=str(exc),
            )
            continue

    raise LLMNotAvailableError(
        "No LLM provider configured. Set OLLAMA_BASE_URL, ANTHROPIC_API_KEY, "
        "OPENAI_API_KEY, or DEEPSEEK_API_KEY in your environment."
    )


# ── Provenance capture wrapper ─────────────────────────────────────────────────


def _token_counts(result: Any) -> tuple[int, int]:
    """(input, output) tokens from a LangChain message's ``usage_metadata``, 0 when absent."""
    usage = getattr(result, "usage_metadata", None)
    if not isinstance(usage, dict):
        return 0, 0
    return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


class _ProvenanceCapturingLLM:
    """
    Wraps a LangChain LLM to auto-capture LLMInvocation metadata on every call.

    Each call -- successful or not -- appends one ``LLMInvocation`` through
    ``src.api.provenance.store_llm_invocation``: session, agent, capability,
    provider, model, the prompt template's id and hash, temperature, token counts
    (when the provider reports them) and latency. Capture is best-effort: a failure
    to record is logged and never fails the call.
    """

    def __init__(
        self,
        llm: Any,
        session_id: str,
        agent_id: str,
        provider: str,
        model_name: str,
        capability: Capability,
        *,
        temperature: float = 0.0,
        prompt_template_id: str | None = None,
        prompt_template_hash: str | None = None,
    ) -> None:
        self._llm = llm
        self._session_id = session_id
        self._agent_id = agent_id
        self._provider = provider
        self._model_name = model_name
        self._capability = capability
        self._temperature = temperature
        self._prompt_template_id = prompt_template_id
        self._prompt_template_hash = prompt_template_hash

    async def ainvoke(self, prompt: Any, **kwargs: Any) -> Any:
        import time as _time

        t0 = _time.perf_counter()
        result: Any = None
        try:
            result = await self._llm.ainvoke(prompt, **kwargs)
        finally:
            self._capture((_time.perf_counter() - t0) * 1000, result)
        return result

    def invoke(self, prompt: Any, **kwargs: Any) -> Any:
        import time as _time

        t0 = _time.perf_counter()
        result: Any = None
        try:
            result = self._llm.invoke(prompt, **kwargs)
        finally:
            self._capture((_time.perf_counter() - t0) * 1000, result)
        return result

    def _capture(self, duration_ms: float, result: Any) -> None:
        """Emit LLMInvocation to the provenance store."""
        try:
            from src.api.provenance import store_llm_invocation
            from src.semantic_layer.provenance import (
                LLMCapability,
                LLMInvocation,
                LLMProvider,
            )

            # Map string capability/provider to enums
            cap_map = {
                "causal_reasoning": LLMCapability.CAUSAL_REASONING,
                "fast_classification": LLMCapability.FAST_CLASSIFICATION,
                "document_extraction": LLMCapability.DOCUMENT_EXTRACTION,
                "code_generation": LLMCapability.CODE_GENERATION,
                "planning": LLMCapability.PLANNING,
            }
            prov_map = {
                "ollama": LLMProvider.OLLAMA,
                "anthropic": LLMProvider.ANTHROPIC,
                "openai": LLMProvider.OPENAI,
                "deepseek": LLMProvider.DEEPSEEK,
            }
            tokens_in, tokens_out = _token_counts(result)

            invocation = LLMInvocation(
                session_id=self._session_id,
                agent_id=self._agent_id,
                capability=cap_map.get(self._capability, LLMCapability.CAUSAL_REASONING),
                provider=prov_map.get(self._provider, LLMProvider.OLLAMA),
                model_name=self._model_name,
                prompt_template_id=self._prompt_template_id,
                prompt_template_hash=self._prompt_template_hash,
                temperature=self._temperature,
                input_token_count=tokens_in,
                output_token_count=tokens_out,
                latency_ms=round(duration_ms, 2),
            )
            store_llm_invocation(invocation)

        except Exception as exc:
            # Provenance capture is best-effort; never block on failure
            log.warning("llm_factory.provenance_capture.failed", error=str(exc))

    # Passthrough attribute access to the underlying LLM
    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)


def _wrap_with_invocation_capture(
    llm: Any,
    session_id: str,
    agent_id: str,
    provider: str,
    model_name: str,
    capability: Capability,
    *,
    temperature: float = 0.0,
    prompt_template_id: str | None = None,
    prompt_template_hash: str | None = None,
) -> Any:
    """Wrap an LLM instance with automatic invocation metadata capture."""
    log.info(
        "llm_factory.provenance_capture.enabled",
        session_id=session_id,
        agent_id=agent_id,
        provider=provider,
        model=model_name,
        prompt_template_id=prompt_template_id,
    )
    return _ProvenanceCapturingLLM(
        llm=llm,
        session_id=session_id,
        agent_id=agent_id,
        provider=provider,
        model_name=model_name,
        capability=capability,
        temperature=temperature,
        prompt_template_id=prompt_template_id,
        prompt_template_hash=prompt_template_hash,
    )
