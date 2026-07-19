from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Callable, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

from rent_bot.taiwan_591_locations import normalize_and_validate_locations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "rent_config.toml"


def _parse_env_bool(_name: str, value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_env_int(name: str, value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _parse_env_float(name: str, value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc


def _parse_env_string(_name: str, value: str) -> str:
    return value


def _parse_env_run_mode(_name: str, value: str) -> str:
    return value.strip().lower()


class Settings(BaseModel):
    discord_webhook_url: str | None = None
    openai_api_key: str | None = None
    google_maps_api_key: str | None = None

    run_mode: Literal["backfill", "watch"] = "watch"
    max_results_per_run: int = Field(default=20, ge=1, le=250)
    watch_notify_limit: int = Field(default=50, ge=1, le=250)
    database_path: Path = PROJECT_ROOT / "rent_bot.sqlite3"

    crawl_interval_minutes: int = Field(default=60, ge=15, le=1440)
    catch_up_grace_minutes: int = Field(default=15, ge=0, le=1440)
    source_591_catch_up_max_pages: int = Field(default=2, ge=1, le=20)

    enable_google_maps: bool = False
    enable_ntu_ranking: bool = True
    enable_openai_classifier: bool = False
    openai_model: str = "gpt-4.1-mini"

    source_ptt_enabled: bool = True
    ptt_max_pages: int = Field(default=3, ge=1, le=20)
    source_591_enabled: bool = False
    source_591_search_urls: list[str] = Field(default_factory=list)
    source_591_room_types: list[Literal["獨立套房", "分租套房"]] = Field(
        default_factory=lambda: ["獨立套房", "分租套房"],
        min_length=1,
    )
    source_591_max_pages: int = Field(default=1, ge=1, le=20)
    source_591_page_size: int = Field(default=30, ge=1, le=100)
    source_591_max_age_days: int = Field(default=14, ge=1, le=365)
    source_rakuya_enabled: bool = False
    source_rakuya_search_urls: list[str] = Field(default_factory=list)
    source_rakuya_max_pages: int = Field(default=1, ge=1, le=20)
    source_rakuya_max_age_days: int = Field(default=14, ge=1, le=365)
    source_yungching_enabled: bool = False
    source_yungching_search_urls: list[str] = Field(default_factory=list)
    source_yungching_max_pages: int = Field(default=1, ge=1, le=20)
    source_yungching_max_age_days: int = Field(default=14, ge=1, le=365)
    source_houseprice_enabled: bool = False
    source_houseprice_search_urls: list[str] = Field(default_factory=list)
    source_houseprice_max_pages: int = Field(default=1, ge=1, le=20)
    source_houseprice_max_age_days: int = Field(default=14, ge=1, le=365)
    source_sinyi_enabled: bool = False
    source_sinyi_search_urls: list[str] = Field(default_factory=list)
    source_sinyi_max_pages: int = Field(default=1, ge=1, le=20)
    source_sinyi_max_age_days: int = Field(default=14, ge=1, le=365)

    request_min_delay_seconds: float = Field(default=2.0, ge=0)
    request_max_delay_seconds: float = Field(default=5.0, ge=0)
    http_timeout_seconds: int = Field(default=20, ge=5, le=120)
    user_agent: str = "TaiwanRentMonitor/0.1 personal-use"

    allowed_city_districts: dict[str, set[str]] = Field(
        default_factory=lambda: {
            "新北市": {"永和區", "新店區"},
            "台北市": {"信義區", "大安區", "文山區", "中山區", "大同區", "松山區", "萬華區"},
        }
    )
    max_rent: int = Field(default=18_600, ge=1)
    min_area_ping: float = Field(default=6.0, ge=0)
    suite_only: bool = True
    exclude_female_only: bool = True

    @field_validator("database_path", mode="before")
    @classmethod
    def _database_path(cls, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @field_validator(
        "source_591_search_urls",
        "source_rakuya_search_urls",
        "source_yungching_search_urls",
        "source_houseprice_search_urls",
        "source_sinyi_search_urls",
        mode="before",
    )
    @classmethod
    def _split_urls(cls, value: str | list[str] | None) -> list[str]:
        if not value:
            return []
        if isinstance(value, list):
            return value
        return [item.strip() for item in value.split(",") if item.strip()]

    @field_validator("source_591_room_types", mode="before")
    @classmethod
    def _split_room_types(cls, value: str | list[str] | None) -> list[str]:
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",") if item.strip()]
        return list(dict.fromkeys(value or []))

    @model_validator(mode="after")
    def _validate_request_delay(self) -> "Settings":
        if self.request_max_delay_seconds < self.request_min_delay_seconds:
            raise ValueError(
                "REQUEST_MAX_DELAY_SECONDS must be greater than or equal to "
                "REQUEST_MIN_DELAY_SECONDS"
            )
        return self

    @model_validator(mode="after")
    def _validate_catch_up_pages(self) -> "Settings":
        if self.source_591_catch_up_max_pages < self.source_591_max_pages:
            if "source_591_catch_up_max_pages" not in self.model_fields_set:
                # Preserve compatibility for callers that only raised the old
                # SOURCE_591_MAX_PAGES setting before catch-up existed.
                self.source_591_catch_up_max_pages = self.source_591_max_pages
            else:
                raise ValueError(
                    "SOURCE_591_CATCH_UP_MAX_PAGES must be greater than or equal to "
                    "SOURCE_591_MAX_PAGES"
                )
        return self


_TOP_LEVEL_TABLES = {
    "classification",
    "filters",
    "locations",
    "network",
    "notifications",
    "ranking",
    "runtime",
    "schedule",
    "sources",
}

_SECTION_MAPPINGS: dict[str, dict[str, str]] = {
    "filters": {
        "max_rent": "max_rent",
        "min_area_ping": "min_area_ping",
        "suite_only": "suite_only",
        "exclude_female_only": "exclude_female_only",
        "allowed_districts": "allowed_city_districts",
    },
    "schedule": {
        "interval_minutes": "crawl_interval_minutes",
        "catch_up_grace_minutes": "catch_up_grace_minutes",
        "source_591_catch_up_max_pages": "source_591_catch_up_max_pages",
    },
    "notifications": {
        "max_results_per_run": "max_results_per_run",
        "watch_notify_limit": "watch_notify_limit",
    },
    "ranking": {
        "enable_google_maps": "enable_google_maps",
        "enable_ntu_ranking": "enable_ntu_ranking",
    },
    "network": {
        "request_min_delay_seconds": "request_min_delay_seconds",
        "request_max_delay_seconds": "request_max_delay_seconds",
        "http_timeout_seconds": "http_timeout_seconds",
        "user_agent": "user_agent",
    },
    "runtime": {
        "run_mode": "run_mode",
        "database_path": "database_path",
    },
    "classification": {
        "enable_openai_classifier": "enable_openai_classifier",
        "openai_model": "openai_model",
    },
}

_SOURCE_MAPPINGS: dict[str, dict[str, str]] = {
    "ptt": {
        "enabled": "source_ptt_enabled",
        "max_pages": "ptt_max_pages",
    },
    "591": {
        "enabled": "source_591_enabled",
        "search_urls": "source_591_search_urls",
        "room_types": "source_591_room_types",
        "max_pages": "source_591_max_pages",
        "page_size": "source_591_page_size",
        "max_age_days": "source_591_max_age_days",
    },
    "rakuya": {
        "enabled": "source_rakuya_enabled",
        "search_urls": "source_rakuya_search_urls",
        "max_pages": "source_rakuya_max_pages",
        "max_age_days": "source_rakuya_max_age_days",
    },
    "yungching": {
        "enabled": "source_yungching_enabled",
        "search_urls": "source_yungching_search_urls",
        "max_pages": "source_yungching_max_pages",
        "max_age_days": "source_yungching_max_age_days",
    },
    "houseprice": {
        "enabled": "source_houseprice_enabled",
        "search_urls": "source_houseprice_search_urls",
        "max_pages": "source_houseprice_max_pages",
        "max_age_days": "source_houseprice_max_age_days",
    },
    "sinyi": {
        "enabled": "source_sinyi_enabled",
        "search_urls": "source_sinyi_search_urls",
        "max_pages": "source_sinyi_max_pages",
        "max_age_days": "source_sinyi_max_age_days",
    },
}


def _resolve_config_path() -> tuple[Path, bool]:
    configured_path = (os.getenv("RENT_CONFIG_PATH") or "").strip()
    if not configured_path:
        return DEFAULT_CONFIG_PATH, False

    path = Path(os.path.expandvars(configured_path)).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve(), True


def _read_toml_config(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise ValueError(f"RENT_CONFIG_PATH points to a missing file: {path}")
        return {}
    if not path.is_file():
        raise ValueError(f"Rent config path is not a file: {path}")

    try:
        with path.open("rb") as file:
            document = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML in rent config {path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Could not read rent config {path}: {exc}") from exc

    version = document.get("schema_version", 1)
    if version != 1:
        raise ValueError(f"Unsupported rent config schema_version {version!r}; expected 1")

    unknown_top_level = set(document) - _TOP_LEVEL_TABLES - {"schema_version"}
    if unknown_top_level:
        names = ", ".join(sorted(unknown_top_level))
        raise ValueError(f"Unsupported top-level rent config key(s): {names}")

    settings_values: dict[str, Any] = {}
    for section_name, mapping in _SECTION_MAPPINGS.items():
        section = _config_table(document, section_name)
        _copy_known_keys(section, mapping, settings_values, f"[{section_name}]")

    sources = _config_table(document, "sources")
    unknown_sources = set(sources) - set(_SOURCE_MAPPINGS)
    if unknown_sources:
        names = ", ".join(sorted(unknown_sources))
        raise ValueError(f"Unsupported source config table(s): {names}")
    for source_name, mapping in _SOURCE_MAPPINGS.items():
        source = _config_table(sources, source_name, parent="sources")
        _copy_known_keys(source, mapping, settings_values, f"[sources.{source_name}]")

    has_locations = "locations" in document
    has_legacy_locations = "allowed_city_districts" in settings_values
    if has_locations and has_legacy_locations:
        raise ValueError(
            "Define locations once: use either [locations] or the legacy "
            "[filters.allowed_districts], not both"
        )
    if has_locations:
        settings_values["allowed_city_districts"] = _validate_allowed_districts(
            _config_table(document, "locations"),
            label="[locations]",
        )
    elif has_legacy_locations:
        settings_values["allowed_city_districts"] = _validate_allowed_districts(
            settings_values["allowed_city_districts"],
            label="[filters.allowed_districts]",
        )
    return settings_values


def _config_table(document: dict[str, Any], name: str, *, parent: str | None = None) -> dict[str, Any]:
    value = document.get(name)
    if value is None:
        return {}
    label = f"[{parent}.{name}]" if parent else f"[{name}]"
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a TOML table")
    return value


def _copy_known_keys(
    section: dict[str, Any],
    mapping: dict[str, str],
    destination: dict[str, Any],
    label: str,
) -> None:
    unknown_keys = set(section) - set(mapping)
    if unknown_keys:
        names = ", ".join(sorted(unknown_keys))
        raise ValueError(f"Unsupported key(s) in {label}: {names}")
    for config_key, settings_key in mapping.items():
        if config_key in section:
            destination[settings_key] = section[config_key]


def _validate_allowed_districts(value: Any, *, label: str) -> dict[str, set[str]]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a TOML table")
    parsed: dict[str, list[str]] = {}
    for city, districts in value.items():
        if not isinstance(city, str) or not city.strip():
            raise ValueError(f"{label} city names must be non-empty strings")
        if not isinstance(districts, list) or not all(isinstance(item, str) and item.strip() for item in districts):
            raise ValueError(f"{label}.{city} must be an array of non-empty strings")
        parsed[city] = districts
    return normalize_and_validate_locations(parsed, label=label)


_ENV_OVERRIDES: tuple[tuple[str, str, Callable[[str, str], Any]], ...] = (
    ("RUN_MODE", "run_mode", _parse_env_run_mode),
    ("MAX_RESULTS_PER_RUN", "max_results_per_run", _parse_env_int),
    ("WATCH_NOTIFY_LIMIT", "watch_notify_limit", _parse_env_int),
    ("DB_PATH", "database_path", _parse_env_string),
    ("CRAWL_INTERVAL_MINUTES", "crawl_interval_minutes", _parse_env_int),
    ("CATCH_UP_GRACE_MINUTES", "catch_up_grace_minutes", _parse_env_int),
    ("SOURCE_591_CATCH_UP_MAX_PAGES", "source_591_catch_up_max_pages", _parse_env_int),
    ("MAX_RENT", "max_rent", _parse_env_int),
    ("MIN_AREA_PING", "min_area_ping", _parse_env_float),
    ("SUITE_ONLY", "suite_only", _parse_env_bool),
    ("EXCLUDE_FEMALE_ONLY", "exclude_female_only", _parse_env_bool),
    ("ENABLE_GOOGLE_MAPS", "enable_google_maps", _parse_env_bool),
    ("ENABLE_NTU_RANKING", "enable_ntu_ranking", _parse_env_bool),
    ("ENABLE_OPENAI_CLASSIFIER", "enable_openai_classifier", _parse_env_bool),
    ("OPENAI_MODEL", "openai_model", _parse_env_string),
    ("SOURCE_PTT_ENABLED", "source_ptt_enabled", _parse_env_bool),
    ("PTT_MAX_PAGES", "ptt_max_pages", _parse_env_int),
    ("SOURCE_591_ENABLED", "source_591_enabled", _parse_env_bool),
    ("SOURCE_591_SEARCH_URLS", "source_591_search_urls", _parse_env_string),
    ("SOURCE_591_ROOM_TYPES", "source_591_room_types", _parse_env_string),
    ("SOURCE_591_MAX_PAGES", "source_591_max_pages", _parse_env_int),
    ("SOURCE_591_PAGE_SIZE", "source_591_page_size", _parse_env_int),
    ("SOURCE_591_MAX_AGE_DAYS", "source_591_max_age_days", _parse_env_int),
    ("SOURCE_RAKUYA_ENABLED", "source_rakuya_enabled", _parse_env_bool),
    ("SOURCE_RAKUYA_SEARCH_URLS", "source_rakuya_search_urls", _parse_env_string),
    ("SOURCE_RAKUYA_MAX_PAGES", "source_rakuya_max_pages", _parse_env_int),
    ("SOURCE_RAKUYA_MAX_AGE_DAYS", "source_rakuya_max_age_days", _parse_env_int),
    ("SOURCE_YUNGCHING_ENABLED", "source_yungching_enabled", _parse_env_bool),
    ("SOURCE_YUNGCHING_SEARCH_URLS", "source_yungching_search_urls", _parse_env_string),
    ("SOURCE_YUNGCHING_MAX_PAGES", "source_yungching_max_pages", _parse_env_int),
    ("SOURCE_YUNGCHING_MAX_AGE_DAYS", "source_yungching_max_age_days", _parse_env_int),
    ("SOURCE_HOUSEPRICE_ENABLED", "source_houseprice_enabled", _parse_env_bool),
    ("SOURCE_HOUSEPRICE_SEARCH_URLS", "source_houseprice_search_urls", _parse_env_string),
    ("SOURCE_HOUSEPRICE_MAX_PAGES", "source_houseprice_max_pages", _parse_env_int),
    ("SOURCE_HOUSEPRICE_MAX_AGE_DAYS", "source_houseprice_max_age_days", _parse_env_int),
    ("SOURCE_SINYI_ENABLED", "source_sinyi_enabled", _parse_env_bool),
    ("SOURCE_SINYI_SEARCH_URLS", "source_sinyi_search_urls", _parse_env_string),
    ("SOURCE_SINYI_MAX_PAGES", "source_sinyi_max_pages", _parse_env_int),
    ("SOURCE_SINYI_MAX_AGE_DAYS", "source_sinyi_max_age_days", _parse_env_int),
    ("REQUEST_MIN_DELAY_SECONDS", "request_min_delay_seconds", _parse_env_float),
    ("REQUEST_MAX_DELAY_SECONDS", "request_max_delay_seconds", _parse_env_float),
    ("HTTP_TIMEOUT_SECONDS", "http_timeout_seconds", _parse_env_int),
    ("USER_AGENT", "user_agent", _parse_env_string),
)


def _apply_environment_overrides(settings_values: dict[str, Any]) -> None:
    for env_name, settings_name, converter in _ENV_OVERRIDES:
        value = os.getenv(env_name)
        if value is None or value == "":
            continue
        settings_values[settings_name] = converter(env_name, value)

    # Secrets intentionally have no TOML mapping. Empty values mean disabled.
    for env_name, settings_name in (
        ("DISCORD_WEBHOOK_URL", "discord_webhook_url"),
        ("OPENAI_API_KEY", "openai_api_key"),
        ("GOOGLE_MAPS_API_KEY", "google_maps_api_key"),
    ):
        value = os.getenv(env_name)
        if value:
            settings_values[settings_name] = value


def get_settings() -> Settings:
    # load_dotenv defaults to override=False, so deployment environment values
    # remain authoritative over the local .env file.
    load_dotenv(PROJECT_ROOT / ".env")
    config_path, required = _resolve_config_path()
    settings_values = _read_toml_config(config_path, required=required)
    _apply_environment_overrides(settings_values)
    return Settings.model_validate(settings_values)
