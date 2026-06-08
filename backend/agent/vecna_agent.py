import os

from agent.tools import analyze_http_security_headers, probe_common_paths, resolve_dns_and_subdomains
from agent.vecna_litellm import VecnaLiteLLMModel
import litellm
from strands import Agent
from app.config import Settings
from app.url_guard import is_safe_public_target

_OPENROUTER_API_BASE_DEFAULT = "https://openrouter.ai/api/v1"


def _validated_gateway_base() -> str:
    """Return the LLM gateway base URL after SSRF validation.

    Reads OPENROUTER_API_BASE from the environment (falling back to the
    official OpenRouter endpoint) and rejects any value that resolves to a
    private/internal address — preventing server-side request forgery via
    environment-variable injection.
    """
    url = (os.environ.get("OPENROUTER_API_BASE") or "").strip() or _OPENROUTER_API_BASE_DEFAULT
    ok, reason = is_safe_public_target(url)
    if not ok:
        raise RuntimeError(
            f"Gateway URL OPENROUTER_API_BASE={url!r} blocked by SSRF guard: {reason}. "
            "Only public HTTPS endpoints are permitted for the LLM gateway."
        )
    return url


SYSTEM_PROMPT = """You are Vecna Ops, a passive security reconnaissance agent.

Given a target URL from the operator:
1. Use resolve_dns_and_subdomains to map DNS and certificate transparency names (passive only).
2. Use analyze_http_security_headers to review security headers (HSTS, CSP, framing, etc.).
3. Use probe_common_paths to check a small set of common URLs (robots.txt, .well-known, etc.).

Rules:
- Only use the provided tools; do not invent scan results.
- Do not perform active exploitation or aggressive scanning.
- After tools complete, write a concise structured markdown report with sections: Executive summary, DNS & names, Headers, Paths, Risk table, Recommendations.

Keep reasoning brief; prefer tool data over speculation."""


def _openrouter_key(s: Settings) -> str | None:
    return (s.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY") or "").strip() or None


def _openai_key(s: Settings) -> str | None:
    return (s.openai_api_key or os.environ.get("OPENAI_API_KEY") or "").strip() or None


def _claude_direct_api_key(s: Settings) -> str | None:
    return (s.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None


def _google_key(s: Settings) -> str | None:
    return (s.google_api_key or os.environ.get("GOOGLE_API_KEY") or "").strip() or None


def _client_args_for_model(s: Settings) -> dict | None:
    mid = (s.litellm_model_id or "").strip()
    if mid.startswith("openrouter/"):
        key = _openrouter_key(s)
        if not key:
            return None
        # Force OpenRouter routing: LiteLLM 1.8x can mis-detect openrouter/... and then use the
        # OpenAI client + OPENAI_API_KEY. See get_llm_provider openrouter branch.
        base = _validated_gateway_base()
        return {
            "api_key": key,
            "api_base": base,
            "base_url": base,
            "custom_llm_provider": "openrouter",
        }
    if mid.startswith("openai/"):
        key = _openai_key(s)
        if not key:
            return None
        return {"api_key": key}
    if mid.startswith("anthropic/"):
        key = _claude_direct_api_key(s)
        if not key:
            return None
        return {"api_key": key}
    if mid.startswith("gemini/"):
        key = _google_key(s)
        if not key:
            return None
        return {"api_key": key}
    return None


def build_agent(settings: Settings) -> Agent:
    mid = settings.litellm_model_id.strip()
    extra = _client_args_for_model(settings)
    if mid.startswith("openrouter/"):
        k = _openrouter_key(settings)
        if not k:
            raise RuntimeError(
                "OpenRouter is configured (LITELLM_MODEL_ID starts with openrouter/) but "
                "OPENROUTER_API_KEY is missing or empty. Check backend/.env and ensure no "
                "empty OPENROUTER_API_KEY is exported in your shell or IDE run configuration."
            )
        litellm.openrouter_key = k
        litellm.api_key = k
        os.environ["OPENAI_API_KEY"] = k
    elif mid.startswith("openai/"):
        if not _openai_key(settings):
            raise RuntimeError(
                "OpenAI is configured (LITELLM_MODEL_ID starts with openai/) but "
                "OPENAI_API_KEY is missing or empty. Set it in backend/.env, or use "
                "LITELLM_MODEL_ID=openrouter/... with OPENROUTER_API_KEY instead."
            )
    elif mid.startswith("anthropic/"):
        if not _claude_direct_api_key(settings):
            raise RuntimeError(
                "Direct Claude API is selected (LITELLM_MODEL_ID uses the anthropic/ provider prefix) "
                "but ANTHROPIC_API_KEY is missing or empty. Set it in backend/.env."
            )
    elif mid.startswith("gemini/"):
        if not _google_key(settings):
            raise RuntimeError(
                "Gemini is configured (LITELLM_MODEL_ID starts with gemini/) but "
                "GOOGLE_API_KEY is missing or empty. Set it in backend/.env."
            )
    model = VecnaLiteLLMModel(model_id=mid, client_args=extra)
    return Agent(
        model=model,
        tools=[
            resolve_dns_and_subdomains,
            analyze_http_security_headers,
            probe_common_paths,
        ],
        system_prompt=SYSTEM_PROMPT,
        name="vecna_recon",
        callback_handler=None,
    )


def user_message_for_target(target_url: str) -> str:
    return f"""Run passive reconnaissance for this target: {target_url}

Confirm the URL is intentional, execute the three tools in a sensible order, then deliver the final report."""
