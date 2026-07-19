from __future__ import annotations

import logging
import re
import time
from datetime import datetime

import requests

from rent_bot.config import Settings
from rent_bot.models import Listing

logger = logging.getLogger(__name__)


DISCORD_WEBHOOK_PATTERN = re.compile(
    r"https://(?:(?:canary|ptb)\.)?(?:discord(?:app)?\.com)/api/webhooks/[^\s/'\"<>]+/[^\s'\"<>]+",
    re.IGNORECASE,
)


def send_test_message(settings: Settings) -> bool:
    embed = {
        "title": "台灣租屋監控測試",
        "description": "Discord webhook 設定正常。",
        "color": 0x2F80ED,
    }
    return _post_embeds(settings, [embed])


def send_text_message(settings: Settings, content: str) -> bool:
    if not content.strip():
        return False
    return _post_payload(settings, {"content": content[:2000]})


def send_listing_embeds(
    settings: Settings,
    listings: list[Listing],
    change_reasons: dict[tuple[str, str], list[str]] | None = None,
) -> int:
    return len(send_listing_embeds_detailed(settings, listings, change_reasons=change_reasons))


def send_listing_embeds_detailed(
    settings: Settings,
    listings: list[Listing],
    change_reasons: dict[tuple[str, str], list[str]] | None = None,
) -> list[Listing]:
    if not listings:
        return []
    sent: list[Listing] = []
    for listing in listings:
        embed = _listing_to_embed(
            listing,
            change_reasons.get((listing.source, listing.listing_id), []) if change_reasons else [],
        )
        if _post_embeds(settings, [embed]):
            sent.append(listing)
        time.sleep(0.6)
    return sent


def _post_embeds(settings: Settings, embeds: list[dict]) -> bool:
    return _post_payload(settings, {"embeds": embeds})


def _post_payload(settings: Settings, payload: dict) -> bool:
    if not settings.discord_webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL is not set; skipping Discord notification")
        return False
    try:
        response = requests.post(settings.discord_webhook_url, json=payload, timeout=settings.http_timeout_seconds)
    except requests.RequestException as exc:
        logger.error("Discord webhook request failed: %s", _redact_webhook(str(exc), settings.discord_webhook_url))
        return False
    if response.status_code == 429:
        retry_after = _discord_retry_after(response)
        logger.warning("Discord webhook rate limited; retrying after %.2fs", retry_after)
        time.sleep(retry_after)
        try:
            response = requests.post(settings.discord_webhook_url, json=payload, timeout=settings.http_timeout_seconds)
        except requests.RequestException as exc:
            logger.error("Discord webhook retry failed: %s", _redact_webhook(str(exc), settings.discord_webhook_url))
            return False
    if response.status_code >= 400:
        response_text = _redact_webhook(response.text[:500], settings.discord_webhook_url)
        logger.error("Discord webhook failed: %s %s", response.status_code, response_text)
        return False
    return True


def _redact_webhook(value: str, webhook_url: str | None = None) -> str:
    redacted = value
    if webhook_url:
        redacted = redacted.replace(webhook_url, "<redacted Discord webhook>")
    return DISCORD_WEBHOOK_PATTERN.sub("<redacted Discord webhook>", redacted)


def _discord_retry_after(response: requests.Response) -> float:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    value = payload.get("retry_after") or response.headers.get("Retry-After") or 2.0
    try:
        return max(1.0, min(float(value), 10.0))
    except (TypeError, ValueError):
        return 2.0


def _listing_to_embed(listing: Listing, change_reasons: list[str]) -> dict:
    rent = _money(listing.rent)
    title = f"{listing.title} - {rent}" if rent != "未知" else listing.title
    fields = [
        {"name": "地區/地址", "value": _location(listing), "inline": False},
        {
            "name": "月租/總月租",
            "value": f"{_money(listing.rent)} / {_money(listing.total_monthly_cost)}",
            "inline": True,
        },
        {"name": "坪數", "value": _value(listing.area_ping, suffix=" 坪"), "inline": True},
        {"name": "房型", "value": listing.room_type or "未知", "inline": True},
        {"name": "樓層", "value": listing.floor or "未知", "inline": True},
        {"name": "是否租補", "value": _bool_mark(listing.has_rent_subsidy), "inline": True},
        {"name": "是否可報稅", "value": _bool_mark(listing.has_tax_registration), "inline": True},
        {"name": "是否獨立洗衣機", "value": _bool_mark(listing.has_independent_washer), "inline": True},
        {"name": "垃圾代收", "value": _bool_mark(listing.has_garbage_collection), "inline": True},
        {"name": "更新時間/首次看到", "value": _seen_value(listing), "inline": False},
    ]
    # Old database rows do not have this marker, so only an explicit opt-out
    # hides the optional NTU-specific field.
    if listing.raw_json.get("ntu_scoring_enabled") is not False:
        fields.insert(-1, {"name": "距台大", "value": _ntu_value(listing), "inline": True})
    if change_reasons:
        fields.insert(0, {"name": "重要更新", "value": "\n".join(change_reasons[:5]), "inline": False})
    extra_images = listing.image_urls[1:3]
    if extra_images:
        fields.append({"name": "其他圖片", "value": "\n".join(extra_images), "inline": False})

    embed = {
        "title": title[:256],
        "url": listing.url,
        "description": (listing.raw_json.get("classifier_summary") or listing.description or "")[:300],
        "fields": fields,
        "footer": {"text": f"{listing.source} | {listing.listing_id}"},
        "color": 0x42A66A if not change_reasons else 0xF2A93B,
    }
    if listing.image_urls:
        embed["image"] = {"url": listing.image_urls[0]}
    return embed


def _money(value: int | None) -> str:
    if value is None:
        return "未知"
    return f"{value:,} 元"


def _value(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "未知"
    return f"{value:g}{suffix}"


def _bool_mark(value: bool | None) -> str:
    if value is True:
        return "✅"
    if value is False:
        return "❌"
    return "未知"


def _ntu_value(listing: Listing) -> str:
    if listing.commute_minutes_to_ntu is not None:
        method = listing.raw_json.get("ntu_score_method")
        suffix = "（Google Routes）" if method == "google_routes" else ""
        return f"{listing.commute_minutes_to_ntu} 分鐘{suffix}"
    if listing.near_ntu_score is not None:
        confidence = {
            "high": "高信心",
            "medium": "中信心",
            "low": "低信心",
            "none": "資料不足",
        }.get(str(listing.raw_json.get("ntu_score_confidence") or ""), "近似")
        reason = str(listing.raw_json.get("ntu_score_reason") or "").strip()
        detail = f"；{reason}" if reason else ""
        return f"近似 {listing.near_ntu_score}/100（{confidence}{detail}）"
    return "未知"


def _seen_value(listing: Listing) -> str:
    return f"{_fmt_dt(listing.last_seen_at)} / {_fmt_dt(listing.first_seen_at)}"


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "未知"
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def _location(listing: Listing) -> str:
    parts = [listing.city or "", listing.district or "", listing.address or ""]
    value = "".join(parts).strip()
    return value or "未知"
