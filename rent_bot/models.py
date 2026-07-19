from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Status = Literal["active", "filtered_out", "inactive", "unknown"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_listing_id(source: str, url: str) -> str:
    digest = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:24]
    return f"{source}:{digest}"


def normalize_city_name(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().replace("臺", "台")


class Listing(BaseModel):
    source: str
    listing_id: str
    url: str
    title: str
    city: str | None = None
    district: str | None = None
    address: str | None = None
    rent: int | None = None
    total_monthly_cost: int | None = None
    area_ping: float | None = None
    room_type: str | None = None
    floor: str | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    has_rent_subsidy: bool | None = None
    has_tax_registration: bool | None = None
    has_independent_washer: bool | None = None
    has_garbage_collection: bool | None = None
    near_ntu_score: int | None = None
    commute_minutes_to_ntu: int | None = None
    image_urls: list[str] = Field(default_factory=list)
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_notified_at: datetime | None = None
    status: Status = "unknown"
    raw_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("city", mode="before")
    @classmethod
    def _city(cls, value: str | None) -> str | None:
        return normalize_city_name(value)

    @field_validator("tags", "image_urls", mode="before")
    @classmethod
    def _list_or_json(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                if isinstance(decoded, list):
                    return [str(item) for item in decoded]
            except json.JSONDecodeError:
                return [value]
        return [str(item) for item in value]

    def monthly_cost_for_filter(self) -> int | None:
        return self.total_monthly_cost or self.rent

    def text_for_classification(self) -> str:
        return "\n".join(
            item
            for item in [
                self.title,
                self.city or "",
                self.district or "",
                self.address or "",
                self.room_type or "",
                self.description,
                " ".join(self.tags),
            ]
            if item
        )


class Classification(BaseModel):
    is_suite: bool
    has_rent_subsidy: bool | None = None
    has_tax_registration: bool | None = None
    has_independent_washer: bool | None = None
    has_garbage_collection: bool | None = None
    red_flags: list[str] = Field(default_factory=list)
    summary: str = ""
    score_reason: str = ""


class UpsertResult(BaseModel):
    listing: Listing
    is_new: bool
    important_changes: list[str] = Field(default_factory=list)
