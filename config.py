from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass, field
from dotenv import load_dotenv


load_dotenv(encoding="utf-8-sig")


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}
PLACEHOLDER_PREFIXES = ("votre_", "format:", "changeme", "change_me")


def _raw_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    lowered = value.lower()
    if any(lowered.startswith(prefix) for prefix in PLACEHOLDER_PREFIXES):
        return None
    return value


def env_str(name: str, default: str = "") -> str:
    return _raw_env(name) or default


def env_optional_str(name: str) -> str | None:
    return _raw_env(name)


def env_secret_list(name: str) -> tuple[str, ...]:
    value = _raw_env(name)
    if not value:
        return ()
    try:
        parts = json.loads(value) if value.startswith("[") else re.split(r"[,;\n]+", value)
        if not isinstance(parts, list) or any(not isinstance(part, str) for part in parts):
            raise ValueError
    except (ValueError, TypeError):
        raise RuntimeError(f"{name} must be a JSON string array or comma-separated keys.") from None
    return tuple(dict.fromkeys(part.strip() for part in parts if part.strip()))


def env_bool(name: str, default: bool = False) -> bool:
    value = _raw_env(name)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise RuntimeError(f"{name} must be a boolean (true/false).")


def env_int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    value = _raw_env(name)
    if value is None:
        parsed = default
    else:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise RuntimeError(f"{name} must be an integer.") from exc
    if min_value is not None and parsed < min_value:
        raise RuntimeError(f"{name} must be >= {min_value}.")
    if max_value is not None and parsed > max_value:
        raise RuntimeError(f"{name} must be <= {max_value}.")
    return parsed


def env_seconds(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    value = _raw_env(name)
    if value is None:
        parsed = default
    else:
        match = re.fullmatch(r"(\d+)\s*([smhdSMHD]?)", value)
        if not match:
            raise RuntimeError(f"{name} must be seconds or a duration like 5m, 2h, 1d.")
        amount = int(match.group(1))
        unit = match.group(2).lower() or "s"
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        parsed = amount * multipliers[unit]
    if min_value is not None and parsed < min_value:
        raise RuntimeError(f"{name} must be >= {min_value} seconds.")
    if max_value is not None and parsed > max_value:
        raise RuntimeError(f"{name} must be <= {max_value} seconds.")
    return parsed


def _parse_admin_ids(value: str | None) -> set[int]:
    if not value:
        return set()
    out: set[int] = set()
    invalid: list[str] = []
    for part in value.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.add(int(p))
        except ValueError:
            invalid.append(p)
    if invalid:
        raise RuntimeError("ADMIN_TELEGRAM_IDS contains non-numeric values.")
    return out


def _parse_quota_map(value: str | None) -> dict[str, int]:
    if not value:
        return {}
    out: dict[str, int] = {}
    for part in value.split(","):
        p = part.strip()
        if not p:
            continue
        if "=" not in p:
            raise RuntimeError("SOURCE_QUOTA_PER_CYCLE must use source=quota pairs.")
        name, val = p.split("=", 1)
        name = name.strip().lower()
        if not name:
            raise RuntimeError("SOURCE_QUOTA_PER_CYCLE contains an empty source name.")
        try:
            quota = int(val.strip())
        except ValueError as exc:
            raise RuntimeError("SOURCE_QUOTA_PER_CYCLE quotas must be integers.") from exc
        if quota < 0:
            raise RuntimeError("SOURCE_QUOTA_PER_CYCLE quotas must be >= 0.")
        out[name] = quota
    return out


@dataclass
class Settings:
    bot_token: str = field(default_factory=lambda: env_str("BOT_TOKEN"))
    database_url: str = field(default_factory=lambda: env_str("DATABASE_URL", "sqlite:///data.db"))
    proxy_url: str | None = field(default_factory=lambda: env_optional_str("PROXY_URL"))
    proxy_fallback_direct: bool = field(default_factory=lambda: env_bool("PROXY_FALLBACK_DIRECT", True))

    premium_free_delay_seconds: int = field(
        default_factory=lambda: env_seconds("PREMIUM_FREE_DELAY_SECONDS", 300, min_value=0)
    )

    enable_geocoding: bool = field(default_factory=lambda: env_bool("ENABLE_GEOCODING", False))
    geocoding_user_agent: str = field(default_factory=lambda: env_str("GEOCODING_USER_AGENT", "immobot/1.0"))

    admin_telegram_ids: set[int] = field(default_factory=set)

    source_quota_per_cycle: dict[str, int] = field(default_factory=dict)
    default_source_quota: int = field(default_factory=lambda: env_int("DEFAULT_SOURCE_QUOTA", 3, min_value=0))
    per_source_concurrency: int = field(default_factory=lambda: env_int("PER_SOURCE_CONCURRENCY", 4, min_value=1, max_value=20))
    scraper_task_timeout_seconds: int = field(
        default_factory=lambda: env_seconds("SCRAPER_TASK_TIMEOUT_SECONDS", 20, min_value=5, max_value=180)
    )
    scheduler_source_backoff_min_seconds: int = field(
        default_factory=lambda: env_seconds("SCHEDULER_SOURCE_BACKOFF_MIN_SECONDS", 60, min_value=1)
    )
    scheduler_source_backoff_max_seconds: int = field(
        default_factory=lambda: env_seconds("SCHEDULER_SOURCE_BACKOFF_MAX_SECONDS", 600, min_value=1)
    )
    source_run_retention_days: int = field(default_factory=lambda: env_int("SOURCE_RUN_RETENTION_DAYS", 14, min_value=1))
    scheduler_interval_seconds: int = field(default_factory=lambda: env_seconds("SCHEDULER_INTERVAL_SECONDS", 1800, min_value=60))

    full_scan_mode: bool = field(default_factory=lambda: env_bool("FULL_SCAN", False))
    expand_location_variants: bool = field(default_factory=lambda: env_bool("EXPAND_LOCATION_VARIANTS", True))
    include_generic_location_pass: bool = field(default_factory=lambda: env_bool("INCLUDE_GENERIC_LOCATION_PASS", False))

    dedupe_window_hours: int = field(default_factory=lambda: env_int("DEDUPE_WINDOW_HOURS", 72, min_value=1))
    min_listing_quality_fields: int = field(default_factory=lambda: env_int("MIN_LISTING_QUALITY_FIELDS", 2, min_value=1, max_value=5))

    # --- Firecrawl CLI bridge -------------------------------------------------
    # Backup renderer for primary Scrapling failures. Explicit credentials are
    # rotated within one deadline; account limits and shared credits still apply.
    firecrawl_enabled: bool = field(default_factory=lambda: env_bool("FIRECRAWL_ENABLED", True))
    firecrawl_api_key: str | None = field(default_factory=lambda: env_optional_str("FIRECRAWL_API_KEY"), repr=False)
    firecrawl_api_keys: tuple[str, ...] = field(default_factory=lambda: env_secret_list("FIRECRAWL_API_KEYS"), repr=False)
    firecrawl_cli_path: str | None = field(default_factory=lambda: env_optional_str("FIRECRAWL_CLI_PATH"))
    firecrawl_timeout_seconds: int = field(
        default_factory=lambda: env_seconds("FIRECRAWL_TIMEOUT_SECONDS", 90, min_value=15, max_value=300)
    )
    firecrawl_cache_ttl_seconds: int = field(
        default_factory=lambda: env_seconds("FIRECRAWL_CACHE_TTL_SECONDS", 300, min_value=0, max_value=86400)
    )
    firecrawl_max_pages: int = field(
        default_factory=lambda: env_int("FIRECRAWL_MAX_PAGES", 2, min_value=1, max_value=10)
    )
    firecrawl_wait_for_ms: int = field(
        default_factory=lambda: env_int("FIRECRAWL_WAIT_FOR_MS", 1200, min_value=0, max_value=15000)
    )
    firecrawl_country: str = field(default_factory=lambda: env_str("FIRECRAWL_COUNTRY", "FR"))
    # CLI accepts only "auto" or "basic"; utils.firecrawl coerces anything else.
    firecrawl_proxy: str = field(default_factory=lambda: env_str("FIRECRAWL_PROXY", "auto"))

    # Initial value only: the admin panel persists its override in the database.
    scrapling_enabled: bool = field(default_factory=lambda: env_bool("SCRAPLING_ENABLED", True))
    scrapling_timeout_seconds: int = field(
        default_factory=lambda: env_seconds("SCRAPLING_TIMEOUT_SECONDS", 25, min_value=5, max_value=120)
    )
    scrapling_browser_path: str | None = field(default_factory=lambda: env_optional_str("SCRAPLING_BROWSER_PATH"))

    flashproxy_enabled: bool = field(default_factory=lambda: env_bool("FLASHPROXY_ENABLED", False))
    flashproxy_api_key: str | None = field(default_factory=lambda: env_optional_str("FLASHPROXY_API_KEY"), repr=False)
    flashproxy_plan_id: str | None = field(default_factory=lambda: env_optional_str("FLASHPROXY_PLAN_ID"))
    flashproxy_plan_ids: tuple[str, ...] = field(default_factory=lambda: env_secret_list("FLASHPROXY_PLAN_IDS"))
    flashproxy_country: str = field(default_factory=lambda: env_str("FLASHPROXY_COUNTRY", "FR"))
    flashproxy_sticky_minutes: int = field(default_factory=lambda: env_int("FLASHPROXY_STICKY_MINUTES", 10, min_value=1, max_value=120))
    flashproxy_timeout_seconds: int = field(default_factory=lambda: env_seconds("FLASHPROXY_TIMEOUT_SECONDS", 12, min_value=3, max_value=30))

    web_site_url: str | None = field(default_factory=lambda: env_optional_str("WEB_SITE_URL"))
    favorite_freshness_target_hours: int = field(default_factory=lambda: env_int("FAVORITE_FRESHNESS_TARGET_HOURS", 6, min_value=6, max_value=48))
    favorite_freshness_max_batch: int = field(default_factory=lambda: env_int("FAVORITE_FRESHNESS_MAX_BATCH", 12, min_value=1, max_value=24))
    web_enabled: bool = field(default_factory=lambda: env_bool("WEB_ENABLED", False))
    web_bind_host: str = field(default_factory=lambda: env_str("WEB_BIND_HOST", "0.0.0.0"))
    web_port: int = field(default_factory=lambda: env_int("WEB_PORT", 8088, min_value=1, max_value=65535))
    web_dist_path: str = field(default_factory=lambda: env_str("WEB_DIST_PATH", "/app/web_dist"))
    web_proxy_origin: str | None = field(default_factory=lambda: env_optional_str("WEB_PROXY_ORIGIN"))
    web_proxy_secret: str | None = field(default_factory=lambda: env_optional_str("WEB_PROXY_SECRET"), repr=False)

    ai_scam_enabled: bool = field(default_factory=lambda: env_bool("AI_SCAM_ENABLED", False))
    ai_scam_endpoint: str | None = field(default_factory=lambda: env_optional_str("AI_SCAM_ENDPOINT"))
    ai_scam_api_key: str | None = field(default_factory=lambda: env_optional_str("AI_SCAM_API_KEY"))
    ai_scam_model: str = field(default_factory=lambda: env_str("AI_SCAM_MODEL", "gpt-4o-mini"))
    ai_scam_timeout: int = field(default_factory=lambda: env_seconds("AI_SCAM_TIMEOUT", 8, min_value=1, max_value=60))

    def __post_init__(self) -> None:
        self.admin_telegram_ids = _parse_admin_ids(_raw_env("ADMIN_TELEGRAM_IDS"))
        self.source_quota_per_cycle = _parse_quota_map(_raw_env("SOURCE_QUOTA_PER_CYCLE"))
        if self.ai_scam_enabled and not self.ai_scam_endpoint:
            self.ai_scam_enabled = False
        self.validate()

    def validate(self) -> None:
        if self.web_proxy_origin or self.web_proxy_secret:
            from urllib.parse import urlsplit
            parsed=urlsplit(self.web_proxy_origin or "")
            if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.path or parsed.query or parsed.fragment:
                raise RuntimeError("WEB_PROXY_ORIGIN must be an HTTPS origin without a trailing slash or path.")
            if not self.web_proxy_secret or len(self.web_proxy_secret)<32:
                raise RuntimeError("WEB_PROXY_SECRET must contain at least 32 characters.")
        if not self.bot_token:
            raise RuntimeError("BOT_TOKEN is not set. Configure it in .env or environment.")
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is empty.")
        if self.proxy_url and not self.proxy_url.startswith(("http://", "https://", "socks5://")):
            raise RuntimeError("PROXY_URL must start with http://, https:// or socks5://.")
        if self.scheduler_source_backoff_min_seconds > self.scheduler_source_backoff_max_seconds:
            raise RuntimeError("SCHEDULER_SOURCE_BACKOFF_MIN_SECONDS must be <= SCHEDULER_SOURCE_BACKOFF_MAX_SECONDS.")
        if not re.fullmatch(r"[A-Za-z]{2}", self.flashproxy_country):
            raise RuntimeError("FLASHPROXY_COUNTRY must be a two-letter country code.")


settings = Settings()
