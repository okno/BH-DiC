"""Fail-closed application configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from bh_dic.language import (
    AddressStyle,
    BotLanguageProfile,
    EmojiMode,
    Language,
    Tone,
    Verbosity,
)
from bh_dic.openai.providers import (
    DEFAULT_GROQ_MODEL,
    DEFAULT_LLAMA_BASE_URL,
    ModelProvider,
    llama_endpoint_is_loopback,
    validate_llama_base_url,
)

EnvironmentName = Literal["production", "development", "test", "mock"]
LogFormat = Literal["json", "text"]
InteractionMode = Literal["slash", "mention", "channel"]


class AppSettings(BaseSettings):
    """Validated configuration loaded from environment variables and ``.env``.

    Normal runtime mode is intentionally fail-closed.  Only an explicit mock mode paired with a
    non-production environment may omit provider credentials and Discord identifiers.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
        enable_decoding=False,
        validate_default=True,
    )

    app_name: str = Field(default="BH-DiC", min_length=1, max_length=80)
    app_env: EnvironmentName = "production"
    app_language: str = Field(default="it", pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    app_timezone: str = "Europe/Rome"
    mock_mode: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: LogFormat = "json"
    data_dir: Path = Path("./var")
    database_url: str = "sqlite+aiosqlite:///./var/db/bh_dic.sqlite3"
    audit_hmac_key: SecretStr | None = None
    encryption_key: SecretStr | None = None

    bot_language: Language = "it"
    bot_tone: Tone = "professional"
    bot_address_style: AddressStyle = "neutral"
    bot_verbosity: Verbosity = "standard"
    bot_emoji_mode: EmojiMode = "off"
    bot_display_name: str | None = Field(default=None, max_length=48)
    bot_opening: str | None = Field(default=None, max_length=120)
    bot_closing: str | None = Field(default=None, max_length=120)

    discord_bot_token: SecretStr | None = None
    discord_application_id: int | None = Field(default=None, gt=0, le=2**64 - 1)
    discord_guild_id: int | None = Field(default=None, gt=0, le=2**64 - 1)
    discord_channel_id: int | None = Field(default=None, gt=0, le=2**64 - 1)
    discord_interaction_mode: InteractionMode = "slash"
    discord_allow_dms: bool = False
    discord_dm_auth_guild_id: int | None = Field(default=None, gt=0, le=2**64 - 1)
    discord_dm_allowed_role_ids: tuple[int, ...] = ()
    discord_mention_channel_ids: tuple[int, ...] = ()
    discord_sensitive_delivery_mode: Literal["ephemeral_only", "dm_or_ephemeral"] = "ephemeral_only"
    discord_publish_sensitive_channel_responses: bool = False
    discord_readonly_role_ids: tuple[int, ...] = ()
    discord_hr_read_role_ids: tuple[int, ...] = ()
    discord_pii_role_ids: tuple[int, ...] = ()
    discord_payroll_role_ids: tuple[int, ...] = ()
    discord_document_metadata_role_ids: tuple[int, ...] = ()
    discord_protected_document_role_ids: tuple[int, ...] = ()
    discord_balance_role_ids: tuple[int, ...] = ()
    discord_hr_write_role_ids: tuple[int, ...] = ()
    discord_iam_role_ids: tuple[int, ...] = ()
    discord_document_role_ids: tuple[int, ...] = ()
    discord_approver_role_ids: tuple[int, ...] = ()
    discord_security_admin_role_ids: tuple[int, ...] = ()
    discord_system_admin_role_ids: tuple[int, ...] = ()

    model_provider: ModelProvider = "openai"
    model_store: bool = False
    model_timeout_seconds: float = Field(default=60, ge=1, le=300)
    model_max_retries: int = Field(default=2, ge=0, le=5)
    model_max_output_tokens: int = Field(default=1200, ge=64, le=8192)
    model_result_rendering: Literal["deterministic"] = "deterministic"
    model_reasoning_effort: Literal["none", "low", "medium", "high"] = "low"

    openai_api_key: SecretStr | None = None
    openai_model: str | None = Field(default=None, min_length=1, max_length=120)
    # Backward-compatible aliases. The model validator mirrors these to the generic
    # settings and rejects conflicting explicit values.
    openai_store: bool = False
    openai_timeout_seconds: float = Field(default=60, ge=1, le=300)
    openai_max_retries: int = Field(default=2, ge=0, le=5)
    openai_max_output_tokens: int = Field(default=1200, ge=64, le=8192)
    openai_result_rendering: Literal["deterministic"] = "deterministic"
    openai_reasoning_effort: Literal["none", "low", "medium", "high"] = "low"

    groq_api_key: SecretStr | None = None
    groq_model: str = Field(default=DEFAULT_GROQ_MODEL, min_length=1, max_length=120)

    llama_base_url: str = DEFAULT_LLAMA_BASE_URL
    llama_model: str | None = Field(default=None, min_length=1, max_length=120)
    llama_api_key: SecretStr | None = None

    dic_base_url: str = "https://secure.dipendentincloud.it"
    dic_username: str | None = Field(default=None, max_length=320)
    dic_password: SecretStr | None = None
    dic_totp_secret: SecretStr | None = None
    dic_session_encryption_key: SecretStr | None = None
    dic_expected_tenant_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=19,
        pattern=r"^[1-9][0-9]{0,18}$",
    )
    dic_headless: bool = True
    dic_locale: str = "it-IT"
    dic_timezone: str = "Europe/Rome"
    dic_login_timeout_seconds: float = Field(default=60, ge=5, le=300)
    dic_navigation_timeout_seconds: float = Field(default=30, ge=5, le=180)
    dic_max_concurrent_browser_operations: int = Field(default=1, ge=1, le=4)
    dic_session_state_path: Path = Path("./var/session/dic_session.enc")
    dic_test_employee_id: str | None = Field(default=None, max_length=128)
    dic_test_tenant_confirmed: bool = False

    enable_read_actions: bool = True
    enable_dic_reconnect: bool = False
    enable_write_actions: bool = False
    enable_live_write_tests: bool = False
    enable_employee_create: bool = False
    enable_employee_update: bool = False
    enable_sensitive_profile_update: bool = False
    enable_contract_write: bool = False
    enable_contract_delete: bool = False
    enable_maturation_write: bool = False
    enable_balance_correction: bool = False
    enable_invite_actions: bool = False
    enable_account_connect: bool = False
    enable_account_disconnect: bool = False
    enable_rbac_write: bool = False
    enable_status_change: bool = False
    enable_notification_state_change: bool = False
    enable_document_upload: bool = False
    enable_document_download: bool = False
    enable_document_update: bool = False
    enable_document_delete: bool = False
    enable_export: bool = False
    enable_employee_delete: bool = False
    require_two_person_approval: bool = True
    pending_action_ttl_minutes: int = Field(default=10, ge=1, le=1440)

    upload_max_mb: int = Field(default=20, ge=1, le=100)
    upload_retention_hours: int = Field(default=24, ge=1, le=168)
    upload_allowed_mime_types: tuple[str, ...] = (
        "application/pdf",
        "image/jpeg",
        "image/png",
    )
    clamav_required: bool = True
    clamav_socket: str | None = Field(default=None, max_length=512)
    save_failure_screenshots: bool = False
    playwright_trace_mode: Literal["off", "on", "retain-on-failure"] = "off"
    trace_retention_hours: int = Field(default=4, ge=1, le=24)
    export_max_mb: int = Field(default=8, ge=1, le=25)

    pid_file: Path = Path("./var/run/bh-dic.pid")
    lock_file: Path = Path("./var/run/bh-dic.lock")
    log_dir: Path = Path("./var/log")

    WRITE_FLAG_FIELDS: ClassVar[tuple[str, ...]] = (
        "enable_employee_create",
        "enable_employee_update",
        "enable_sensitive_profile_update",
        "enable_contract_write",
        "enable_contract_delete",
        "enable_maturation_write",
        "enable_balance_correction",
        "enable_invite_actions",
        "enable_account_connect",
        "enable_account_disconnect",
        "enable_rbac_write",
        "enable_status_change",
        "enable_notification_state_change",
        "enable_document_upload",
        "enable_document_download",
        "enable_document_update",
        "enable_document_delete",
        "enable_export",
        "enable_employee_delete",
    )

    MODEL_TUNING_ALIASES: ClassVar[tuple[tuple[str, str, str, str], ...]] = (
        ("model_store", "openai_store", "MODEL_STORE", "OPENAI_STORE"),
        (
            "model_timeout_seconds",
            "openai_timeout_seconds",
            "MODEL_TIMEOUT_SECONDS",
            "OPENAI_TIMEOUT_SECONDS",
        ),
        (
            "model_max_retries",
            "openai_max_retries",
            "MODEL_MAX_RETRIES",
            "OPENAI_MAX_RETRIES",
        ),
        (
            "model_max_output_tokens",
            "openai_max_output_tokens",
            "MODEL_MAX_OUTPUT_TOKENS",
            "OPENAI_MAX_OUTPUT_TOKENS",
        ),
        (
            "model_result_rendering",
            "openai_result_rendering",
            "MODEL_RESULT_RENDERING",
            "OPENAI_RESULT_RENDERING",
        ),
        (
            "model_reasoning_effort",
            "openai_reasoning_effort",
            "MODEL_REASONING_EFFORT",
            "OPENAI_REASONING_EFFORT",
        ),
    )

    @field_validator(
        "discord_readonly_role_ids",
        "discord_hr_read_role_ids",
        "discord_pii_role_ids",
        "discord_payroll_role_ids",
        "discord_document_metadata_role_ids",
        "discord_protected_document_role_ids",
        "discord_balance_role_ids",
        "discord_hr_write_role_ids",
        "discord_iam_role_ids",
        "discord_document_role_ids",
        "discord_approver_role_ids",
        "discord_security_admin_role_ids",
        "discord_system_admin_role_ids",
        "discord_dm_allowed_role_ids",
        "discord_mention_channel_ids",
        mode="before",
    )
    @classmethod
    def parse_discord_ids(cls, value: Any) -> tuple[int, ...]:
        if value is None or value == "":
            return ()
        parts = value.split(",") if isinstance(value, str) else value
        try:
            identifiers = tuple(
                dict.fromkeys(int(str(item).strip()) for item in parts if str(item).strip())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Discord role IDs must be comma-separated positive integers") from exc
        if any(identifier <= 0 or identifier > 2**64 - 1 for identifier in identifiers):
            raise ValueError("Discord role IDs must be positive integers within the 64-bit range")
        return identifiers

    @field_validator("upload_allowed_mime_types", mode="before")
    @classmethod
    def parse_mime_types(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            values = tuple(item.strip().lower() for item in value.split(",") if item.strip())
        else:
            values = tuple(str(item).strip().lower() for item in value)
        if not values or any("/" not in item or len(item) > 127 for item in values):
            raise ValueError("UPLOAD_ALLOWED_MIME_TYPES contains an invalid MIME type")
        return tuple(dict.fromkeys(values))

    @field_validator("app_timezone", "dic_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    @field_validator("llama_base_url")
    @classmethod
    def validate_local_model_base_url(cls, value: str) -> str:
        return validate_llama_base_url(value)

    @field_validator("database_url")
    @classmethod
    def validate_async_database_url(cls, value: str) -> str:
        try:
            driver = make_url(value).drivername
        except Exception as exc:
            raise ValueError("DATABASE_URL is not a valid SQLAlchemy URL") from exc
        if driver not in {"sqlite+aiosqlite", "postgresql+asyncpg"}:
            raise ValueError("DATABASE_URL must use sqlite+aiosqlite or postgresql+asyncpg")
        return value

    @field_validator("dic_base_url")
    @classmethod
    def validate_dic_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "secure.dipendentincloud.it"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("DIC_BASE_URL must be the secure.dipendentincloud.it HTTPS origin")
        return "https://secure.dipendentincloud.it"

    @model_validator(mode="after")
    def enforce_security_invariants(self) -> AppSettings:
        self._synchronize_model_tuning_aliases()
        # Constructing the closed profile validates free-form decorations before
        # any provider, Discord client, or log boundary is created.
        _ = self.language_profile
        if self.model_store:
            raise ValueError(
                "MODEL_STORE=true or OPENAI_STORE=true is forbidden; "
                "provider storage must remain disabled"
            )
        if (
            self.model_provider == "llama"
            and not llama_endpoint_is_loopback(self.llama_base_url)
            and self._is_missing(self.llama_api_key)
        ):
            raise ValueError("LLAMA_API_KEY is required for a remote LLAMA_BASE_URL")

        if self.mock_mode and self.app_env not in {"test", "development", "mock"}:
            raise ValueError("MOCK_MODE may only be used with APP_ENV=test, development, or mock")

        if (
            self.discord_publish_sensitive_channel_responses
            and self.discord_interaction_mode != "channel"
        ):
            raise ValueError(
                "DISCORD_PUBLISH_SENSITIVE_CHANNEL_RESPONSES requires "
                "DISCORD_INTERACTION_MODE=channel"
            )

        if self.discord_allow_dms:
            if self.discord_dm_auth_guild_id != self.discord_guild_id:
                raise ValueError(
                    "DISCORD_DM_AUTH_GUILD_ID must equal DISCORD_GUILD_ID when DMs are enabled"
                )
            if not self.discord_dm_allowed_role_ids:
                raise ValueError(
                    "DISCORD_DM_ALLOWED_ROLE_IDS is required when Discord DMs are enabled"
                )
        if self.discord_publish_sensitive_channel_responses and not self.discord_hr_read_role_ids:
            raise ValueError(
                "DISCORD_PUBLISH_SENSITIVE_CHANNEL_RESPONSES requires at least one explicit "
                "DISCORD_HR_READ_ROLE_IDS value"
            )

        if not self.mock_mode and (
            self.playwright_trace_mode != "off" or self.save_failure_screenshots
        ):
            raise ValueError(
                "Live DIC operation requires PLAYWRIGHT_TRACE_MODE=off and "
                "SAVE_FAILURE_SCREENSHOTS=false"
            )

        enabled_write_flags = [name for name in self.WRITE_FLAG_FIELDS if getattr(self, name)]
        if enabled_write_flags and not self.enable_write_actions:
            raise ValueError(
                "Specific write flags require ENABLE_WRITE_ACTIONS=true: "
                + ", ".join(sorted(enabled_write_flags))
            )

        critical_flags = (
            self.enable_employee_delete,
            self.enable_contract_delete,
            self.enable_balance_correction,
            self.enable_rbac_write,
            self.enable_document_delete,
            self.enable_account_disconnect,
        )
        if any(critical_flags) and not self.require_two_person_approval:
            raise ValueError("Critical actions require REQUIRE_TWO_PERSON_APPROVAL=true")
        if self.enable_document_upload and not self.clamav_required:
            raise ValueError("Document upload requires CLAMAV_REQUIRED=true")
        if self.enable_live_write_tests:
            if not self.enable_write_actions:
                raise ValueError("Live write tests require ENABLE_WRITE_ACTIONS=true")
            if not self.dic_test_employee_id or not self.dic_test_tenant_confirmed:
                raise ValueError(
                    "Live write tests require DIC_TEST_EMPLOYEE_ID and "
                    "DIC_TEST_TENANT_CONFIRMED=true"
                )

        if not self.mock_mode:
            missing = self._missing_runtime_values()
            if missing:
                raise ValueError("Missing required runtime configuration: " + ", ".join(missing))
            self._reject_placeholders()
            self._validate_secret_strength()
        return self

    def _synchronize_model_tuning_aliases(self) -> None:
        explicitly_set = self.model_fields_set
        for generic, legacy, generic_env, legacy_env in self.MODEL_TUNING_ALIASES:
            generic_value = getattr(self, generic)
            legacy_value = getattr(self, legacy)
            generic_set = generic in explicitly_set
            legacy_set = legacy in explicitly_set
            if generic_set and legacy_set and generic_value != legacy_value:
                raise ValueError(f"Conflicting {generic_env} and legacy {legacy_env} values")
            effective = generic_value if generic_set or not legacy_set else legacy_value
            object.__setattr__(self, generic, effective)
            object.__setattr__(self, legacy, effective)

    def _missing_runtime_values(self) -> list[str]:
        required: dict[str, object | None] = {
            "AUDIT_HMAC_KEY": self.audit_hmac_key,
            "ENCRYPTION_KEY": self.encryption_key,
            "DISCORD_BOT_TOKEN": self.discord_bot_token,
            "DISCORD_APPLICATION_ID": self.discord_application_id,
            "DISCORD_GUILD_ID": self.discord_guild_id,
            "DISCORD_CHANNEL_ID": self.discord_channel_id,
            "DIC_USERNAME": self.dic_username,
            "DIC_PASSWORD": self.dic_password,
            "DIC_SESSION_ENCRYPTION_KEY": self.dic_session_encryption_key,
            "DIC_EXPECTED_TENANT_ID": self.dic_expected_tenant_id,
        }
        if self.model_provider == "openai":
            required.update(
                {
                    "OPENAI_API_KEY": self.openai_api_key,
                    "OPENAI_MODEL": self.openai_model,
                }
            )
        elif self.model_provider == "groq":
            required.update(
                {
                    "GROQ_API_KEY": self.groq_api_key,
                    "GROQ_MODEL": self.groq_model,
                }
            )
        else:
            required["LLAMA_MODEL"] = self.llama_model
            if not llama_endpoint_is_loopback(self.llama_base_url):
                required["LLAMA_API_KEY"] = self.llama_api_key
        return sorted(name for name, value in required.items() if self._is_missing(value))

    @staticmethod
    def _is_missing(value: object | None) -> bool:
        if value is None:
            return True
        if isinstance(value, SecretStr):
            return not value.get_secret_value().strip()
        return isinstance(value, str) and not value.strip()

    def _validate_secret_strength(self) -> None:
        keys = {
            "AUDIT_HMAC_KEY": self.audit_hmac_key,
            "ENCRYPTION_KEY": self.encryption_key,
            "DIC_SESSION_ENCRYPTION_KEY": self.dic_session_encryption_key,
        }
        for name, key in keys.items():
            if key is None:
                raise ValueError(f"{name} is required")
            if len(key.get_secret_value().encode("utf-8")) < 32:
                raise ValueError(f"{name} must contain at least 32 UTF-8 bytes")

    def _reject_placeholders(self) -> None:
        values: dict[str, str | SecretStr | None] = {
            "AUDIT_HMAC_KEY": self.audit_hmac_key,
            "ENCRYPTION_KEY": self.encryption_key,
            "DISCORD_BOT_TOKEN": self.discord_bot_token,
            "OPENAI_API_KEY": self.openai_api_key,
            "OPENAI_MODEL": self.openai_model,
            "GROQ_API_KEY": self.groq_api_key,
            "GROQ_MODEL": self.groq_model,
            "LLAMA_API_KEY": self.llama_api_key,
            "LLAMA_MODEL": self.llama_model,
            "DIC_USERNAME": self.dic_username,
            "DIC_PASSWORD": self.dic_password,
            "DIC_SESSION_ENCRYPTION_KEY": self.dic_session_encryption_key,
        }
        rejected = {"changeme", "change_me", "placeholder", "replace_me", "todo", "example"}
        for name, value in values.items():
            if value is None:
                continue
            raw = value.get_secret_value() if isinstance(value, SecretStr) else value
            normalized = raw.strip().casefold()
            if normalized in rejected or (normalized.startswith("<") and normalized.endswith(">")):
                raise ValueError(f"{name} contains a placeholder")

    @property
    def write_flags(self) -> dict[str, bool]:
        """Return feature states without exposing any secret values."""

        return {name: bool(getattr(self, name)) for name in self.WRITE_FLAG_FIELDS}

    @property
    def language_profile(self) -> BotLanguageProfile:
        """Return the validated, closed presentation profile without provider I/O."""

        return BotLanguageProfile(
            language=self.bot_language,
            tone=self.bot_tone,
            address_style=self.bot_address_style,
            verbosity=self.bot_verbosity,
            emoji_mode=self.bot_emoji_mode,
            display_name=self.bot_display_name,
            opening=self.bot_opening,
            closing=self.bot_closing,
        )

    def safe_summary(self) -> dict[str, object]:
        """Return operator-safe configuration metadata."""

        return {
            "app_name": self.app_name,
            "app_env": self.app_env,
            "mock_mode": self.mock_mode,
            "database_driver": make_url(self.database_url).drivername,
            "discord_guild_configured": self.discord_guild_id is not None,
            "discord_channel_configured": self.discord_channel_id is not None,
            "bot_language": self.bot_language,
            "bot_tone": self.bot_tone,
            "bot_address_style": self.bot_address_style,
            "bot_verbosity": self.bot_verbosity,
            "bot_emoji_mode": self.bot_emoji_mode,
            "bot_display_name_configured": self.bot_display_name is not None,
            "bot_opening_configured": self.bot_opening is not None,
            "bot_closing_configured": self.bot_closing is not None,
            "model_provider": self.model_provider,
            "model_configured": self.selected_model is not None,
            "model_store": self.model_store,
            "openai_model_configured": bool(self.openai_model),
            "openai_store": self.openai_store,
            "dic_expected_tenant_configured": bool(self.dic_expected_tenant_id),
            "read_actions": self.enable_read_actions,
            "write_actions": self.enable_write_actions,
            "enabled_write_flags": sorted(
                name for name, enabled in self.write_flags.items() if enabled
            ),
        }

    @property
    def selected_model(self) -> str | None:
        """Return only the selected non-secret model identifier."""

        if self.model_provider == "openai":
            return self.openai_model
        if self.model_provider == "groq":
            return self.groq_model
        return self.llama_model


Settings = AppSettings


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Load and cache the validated runtime configuration."""

    return AppSettings()


def clear_settings_cache() -> None:
    """Clear the settings cache, primarily for isolated tests and CLI validation."""

    get_settings.cache_clear()
