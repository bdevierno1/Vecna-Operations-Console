import os
from pathlib import Path
from typing import Self

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve backend/ so .env loads even when uvicorn is started from another cwd.
_backend_dir = Path(__file__).resolve().parent.parent
_env_file = _backend_dir / ".env"

# Populate os.environ for libraries (e.g. LiteLLM) that read keys directly.
# override=True: if the shell exports empty placeholders (e.g. OPENROUTER_API_KEY=),
# they would otherwise block values from backend/.env (python-dotenv default).
load_dotenv(_env_file, override=True)

# Patch LiteLLM before any code imports litellm (e.g. via strands).
import agent.vecna_litellm  # noqa: E402, F401


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_env_file),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite+aiosqlite:///./vecna.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # When True, hide dollar amounts in the UI (cost visibility toggle).
    vecna_hide_costs: bool = Field(default=False, validation_alias="VECNA_HIDE_COSTS")

    # Lead-scoring flag (default off); LEAD_SCORING_KILL_SWITCH=1 overrides.
    lead_scoring_enabled: bool = Field(default=False, validation_alias="LEAD_SCORING_ENABLED")
    lead_scoring_kill_switch: bool = Field(default=False, validation_alias="LEAD_SCORING_KILL_SWITCH")

    # Platform pricing on top of LLM token cost (USD). Zero by default.
    vecna_scan_fee_usd: float = Field(default=0.0, validation_alias="VECNA_SCAN_FEE_USD")
    # Legacy: single $/call when no tiered JSON is set (VECNA_TOOL_FAMILY_FEES_JSON / OVERRIDES / BY_TOOL).
    vecna_tool_unit_fee_usd: float = Field(default=0.0, validation_alias="VECNA_TOOL_UNIT_FEE_USD")
    # Tiered tool pricing: JSON maps merged with built-in defaults in app.pricing.
    vecna_tool_family_fees_json: str = Field(default="{}", validation_alias="VECNA_TOOL_FAMILY_FEES_JSON")
    vecna_tool_family_by_tool_json: str = Field(default="{}", validation_alias="VECNA_TOOL_FAMILY_BY_TOOL_JSON")
    vecna_tool_fee_overrides_json: str = Field(default="{}", validation_alias="VECNA_TOOL_FEE_OVERRIDES_JSON")

    # Use openrouter/... ids with OPENROUTER_API_KEY, or openai/... with OPENAI_API_KEY, etc.
    litellm_model_id: str = "openai/gpt-4o-mini"

    # Declared so .env values are loaded into Settings and passed explicitly to LiteLLM.
    openrouter_api_key: str | None = None
    # OpenRouter optional headers (LiteLLM reads OR_SITE_URL / OR_APP_NAME for HTTP-Referer / X-Title).
    openrouter_site_url: str = Field(
        default="http://localhost:5173",
        validation_alias=AliasChoices("OPENROUTER_SITE_URL", "OR_SITE_URL"),
    )
    openrouter_app_name: str = Field(
        default="Vecna Ops",
        validation_alias=AliasChoices("OPENROUTER_APP_NAME", "OR_APP_NAME"),
    )
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None

    @field_validator(
        "litellm_model_id",
        "openrouter_api_key",
        "openrouter_site_url",
        "openrouter_app_name",
        "openai_api_key",
        "anthropic_api_key",
        "google_api_key",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    @model_validator(mode="after")
    def export_keys_to_environ(self) -> Self:
        """LiteLLM often reads OPENROUTER_API_KEY from os.environ; keep it in sync with .env."""
        if self.openrouter_api_key:
            os.environ["OPENROUTER_API_KEY"] = self.openrouter_api_key
        if self.openai_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_api_key
        # OpenRouter uses an OpenAI-compatible API; some LiteLLM/strands paths only consult
        # OPENAI_API_KEY. Copy the OpenRouter secret so those paths authenticate (do not use
        # alongside a real OpenAI key for the same process).
        if self.litellm_model_id.startswith("openrouter/") and self.openrouter_api_key:
            os.environ["OPENAI_API_KEY"] = self.openrouter_api_key
            # acompletion() runs sync completion() in a thread pool; that path resolves keys from
            # litellm.api_key / litellm.openrouter_key before per-request kwargs (see litellm.main).
            import litellm as _litellm

            _litellm.openrouter_key = self.openrouter_api_key
            _litellm.api_key = self.openrouter_api_key
        if self.litellm_model_id.startswith("openrouter/"):
            os.environ["OR_SITE_URL"] = self.openrouter_site_url
            os.environ["OR_APP_NAME"] = self.openrouter_app_name
        if self.anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = self.anthropic_api_key
        if self.google_api_key:
            os.environ["GOOGLE_API_KEY"] = self.google_api_key
        return self
