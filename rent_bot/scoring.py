from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from rent_bot.config import Settings
from rent_bot.models import Listing

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RoadRule:
    basis: str
    label: str
    terms: tuple[str, ...]
    address_score: int
    districts: frozenset[str]


@dataclass(frozen=True)
class _StationRule:
    basis: str
    label: str
    terms: tuple[str, ...]
    score: int


_DAAN = frozenset({"大安區"})
_ZHONGZHENG = frozenset({"中正區"})
_DAAN_OR_ZHONGZHENG = _DAAN | _ZHONGZHENG

# These rules deliberately require an exact road and, for long roads, an exact
# section.  A generic mention of 羅斯福路 or 辛亥 must not make a listing look
# close to NTU when it may be several sections away.
ROAD_RULES: tuple[_RoadRule, ...] = (
    # Official NTU campus edges and entrances.
    _RoadRule("campus_edge:roosevelt_4", "羅斯福路四段", ("羅斯福路4段",), 96, _DAAN_OR_ZHONGZHENG),
    _RoadRule("campus_edge:zhoushan", "舟山路側門", ("舟山路",), 96, _DAAN_OR_ZHONGZHENG),
    _RoadRule("campus_edge:xinsheng_south_3", "新生南路三段", ("新生南路3段",), 94, _DAAN),
    _RoadRule("campus_edge:xinhai_2", "辛亥路二段", ("辛亥路2段",), 92, _DAAN),
    _RoadRule("campus_edge:keelung_4", "基隆路四段", ("基隆路4段",), 90, _DAAN),
    _RoadRule("campus_gate:changxing", "長興街校門", ("長興街",), 90, _DAAN),
    # Walkable streets and approach corridors around the main campus.
    _RoadRule("walkable:wenzhou", "溫州街", ("溫州街",), 88, _DAAN),
    _RoadRule("walkable:taishun", "泰順街", ("泰順街",), 86, _DAAN),
    _RoadRule("walkable:tingzhou_3", "汀州路三段", ("汀州路3段",), 86, _DAAN_OR_ZHONGZHENG),
    _RoadRule("walkable:roosevelt_3", "羅斯福路三段", ("羅斯福路3段",), 84, _DAAN_OR_ZHONGZHENG),
    _RoadRule("walkable:xinhai_1", "辛亥路一段", ("辛亥路1段",), 82, _DAAN_OR_ZHONGZHENG),
    _RoadRule("walkable:shida", "師大路", ("師大路",), 82, _DAAN),
    _RoadRule("walkable:longquan", "龍泉街", ("龍泉街",), 82, _DAAN),
    _RoadRule("walkable:siyuan", "思源街", ("思源街",), 82, _ZHONGZHENG),
    _RoadRule("walkable:pucheng", "浦城街", ("浦城街",), 80, _DAAN),
    _RoadRule("approach:fuxing_south_2", "復興南路二段", ("復興南路2段",), 78, _DAAN),
    _RoadRule("approach:heping_east_2", "和平東路二段", ("和平東路2段",), 76, _DAAN),
)


# Station scores are conservative door-to-campus approximations.  The ordering
# follows official Taipei Metro ride times to Gongguan, except Technology
# Building, Liuzhangli and Linguang, which have a more useful walk toward NTU's
# Xinhai entrance.  Only an explicit station phrase is accepted.
STATION_RULES: tuple[_StationRule, ...] = (
    _StationRule("mrt:g07_gongguan", "公館站", ("公館站", "捷運公館", "公館捷運"), 90),
    _StationRule(
        "mrt:g08_taipower_building",
        "台電大樓站",
        ("台電大樓站", "捷運台電大樓", "台電大樓捷運"),
        84,
    ),
    _StationRule(
        "mrt:br08_technology_building",
        "科技大樓站",
        ("科技大樓站", "捷運科技大樓", "科技大樓捷運"),
        84,
    ),
    _StationRule("mrt:g06_wanlong", "萬隆站", ("萬隆站", "捷運萬隆", "萬隆捷運"), 82),
    _StationRule("mrt:g09_guting", "古亭站", ("古亭站", "捷運古亭", "古亭捷運"), 78),
    _StationRule("mrt:g05_jingmei", "景美站", ("景美站", "捷運景美", "景美捷運"), 76),
    _StationRule("mrt:g04_dapinglin", "大坪林站", ("大坪林站", "捷運大坪林", "大坪林捷運"), 70),
    _StationRule("mrt:br07_liuzhangli", "六張犁站", ("六張犁站", "捷運六張犁", "六張犁捷運"), 68),
    _StationRule("mrt:g03_qizhang", "七張站", ("七張站", "捷運七張", "七張捷運"), 66),
    _StationRule("mrt:o04_dingxi", "頂溪站", ("頂溪站", "捷運頂溪", "頂溪捷運"), 64),
    _StationRule("mrt:br06_linguang", "麟光站", ("麟光站", "捷運麟光", "麟光捷運"), 62),
    _StationRule(
        "mrt:g02_xindian_district_office",
        "新店區公所站",
        ("新店區公所站", "捷運新店區公所", "新店區公所捷運"),
        60,
    ),
    _StationRule("mrt:br05_xinhai", "辛亥站", ("辛亥站", "捷運辛亥", "辛亥捷運"), 58),
    _StationRule("mrt:g01_xindian", "新店站", ("新店站", "捷運新店", "新店捷運"), 55),
)


# A district spans too much area to justify a high proximity score.  These are
# intentionally capped at 50 and are used only when no road or station evidence
# is available.
DISTRICT_BASE: dict[tuple[str, str], int] = {
    ("台北市", "大安區"): 50,
    ("台北市", "中正區"): 48,
    ("新北市", "永和區"): 42,
    ("台北市", "文山區"): 40,
    ("新北市", "新店區"): 32,
    ("台北市", "萬華區"): 30,
    ("台北市", "信義區"): 28,
    ("台北市", "松山區"): 24,
    ("台北市", "中山區"): 22,
    ("台北市", "大同區"): 18,
}

_KNOWN_DISTRICTS = tuple(dict.fromkeys(district for _, district in DISTRICT_BASE))
_SECTION_NUMBERS = {
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
}


def enrich_ntu_distance(listing: Listing, settings: Settings) -> Listing:
    listing.raw_json["ntu_scoring_enabled"] = settings.enable_ntu_ranking
    if not settings.enable_ntu_ranking:
        listing.near_ntu_score = None
        listing.commute_minutes_to_ntu = None
        for key in (
            "ntu_score_method",
            "ntu_score_basis",
            "ntu_score_confidence",
            "ntu_score_reason",
        ):
            listing.raw_json.pop(key, None)
        return listing

    # A district centroid is not an address.  Only call the paid route service
    # when the source supplied an address; otherwise use the honest fallback.
    if settings.enable_google_maps and settings.google_maps_api_key and listing.address:
        try:
            from rent_bot.optional.google_maps import estimate_minutes_to_ntu

            minutes = estimate_minutes_to_ntu(
                _route_origin(listing),
                settings.google_maps_api_key,
                settings.http_timeout_seconds,
            )
            if minutes is not None:
                listing.commute_minutes_to_ntu = minutes
                listing.near_ntu_score = _score_from_minutes(minutes)
                _record_score_basis(
                    listing,
                    method="google_routes",
                    basis="google_routes:transit_to_ntu_main_campus",
                    confidence="high",
                    reason=f"Google Routes 大眾運輸估算 {minutes} 分鐘",
                )
                return listing
        except Exception as exc:  # noqa: BLE001
            logger.warning("Google Maps distance failed for %s: %s", listing.url, exc)

    listing.near_ntu_score = fallback_near_ntu_score(listing)
    return listing


def fallback_near_ntu_score(listing: Listing) -> int | None:
    """Estimate NTU proximity from one strongest available piece of evidence.

    Scores are never formed by adding marketing keywords.  This prevents text
    such as ``近台大、近捷運`` from turning a distant or unknown address into a
    near-perfect result.
    """

    address_text = _normalize_location_text(listing.address or "")
    context_text = _normalize_location_text(_context_text(listing))
    city, district = _effective_location(listing, address_text)

    address_rule = _best_road_rule(address_text, city, district)
    if address_rule is not None:
        confidence = "high" if _has_address_detail(address_text) else "medium"
        score = address_rule.address_score if confidence == "high" else address_rule.address_score - 2
        _record_score_basis(
            listing,
            method="address_road",
            basis=f"road:{address_rule.basis}",
            confidence=confidence,
            reason=f"地址符合台大周邊路段：{address_rule.label}",
        )
        return score

    context_city, context_district = _context_location(city, district, context_text)
    context_rule = _best_road_rule(context_text, context_city, context_district)
    if context_rule is not None:
        # Context may be ad copy rather than the actual address.  Keep it below
        # a precise address and record the low confidence explicitly.
        score = min(78, max(0, context_rule.address_score - 18))
        _record_score_basis(
            listing,
            method="context_road",
            basis=f"context_road:{context_rule.basis}",
            confidence="low",
            reason=f"標題或描述提到台大周邊路段：{context_rule.label}（未由地址確認）",
        )
        return score

    address_station = _best_station_rule(address_text)
    if address_station is not None:
        _record_score_basis(
            listing,
            method="mrt_station",
            basis=address_station.basis,
            confidence="medium",
            reason=f"地址明確提到捷運{address_station.label}",
        )
        return address_station.score

    context_station = _best_station_rule(context_text)
    if context_station is not None:
        score = min(78, max(0, context_station.score - 12))
        _record_score_basis(
            listing,
            method="mrt_station",
            basis=context_station.basis,
            confidence="low",
            reason=f"標題或描述提到捷運{context_station.label}（未由地址確認）",
        )
        return score

    district_score = DISTRICT_BASE.get((city or "", district or ""))
    if district_score is not None:
        _record_score_basis(
            listing,
            method="district",
            basis=f"district:{city}/{district}",
            confidence="low",
            reason=f"僅能依行政區粗估：{city}{district}",
        )
        return district_score

    _record_score_basis(
        listing,
        method="unknown",
        basis="unknown:insufficient_location",
        confidence="none",
        reason="地址、明確捷運站與可用行政區資料皆不足",
    )
    return None


def rank_score(listing: Listing, max_rent: int = 18_600) -> float:
    score = float(listing.near_ntu_score or 0)
    cost = listing.total_monthly_cost or listing.rent
    if cost:
        score += max(0, (max_rent - cost) / 300)
    if listing.area_ping:
        score += min(15, listing.area_ping)
    if listing.has_rent_subsidy:
        score += 12
    if listing.has_tax_registration:
        score += 8
    if listing.has_independent_washer:
        score += 10
    if listing.has_garbage_collection:
        score += 5
    if listing.commute_minutes_to_ntu is not None and listing.commute_minutes_to_ntu <= 15:
        score += 15
    return score


def _score_from_minutes(minutes: int) -> int:
    if minutes <= 15:
        return 100
    if minutes <= 25:
        return 85
    if minutes <= 35:
        return 70
    if minutes <= 50:
        return 50
    return 30


def _normalize_location_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).replace("臺", "台")
    # A recurring source typo uses 汀洲路; the official road name is 汀州路.
    normalized = normalized.replace("汀洲路", "汀州路")
    for chinese, digit in _SECTION_NUMBERS.items():
        normalized = normalized.replace(f"{chinese}段", f"{digit}段")
    return re.sub(r"[\s\-–—_/]+", "", normalized)


def _context_text(listing: Listing) -> str:
    return "\n".join(
        part
        for part in (
            listing.title,
            listing.room_type or "",
            listing.description,
            " ".join(listing.tags),
        )
        if part
    )


def _best_road_rule(
    text: str,
    city: str | None,
    district: str | None,
) -> _RoadRule | None:
    if not text or (city and city != "台北市"):
        return None
    matches = [
        rule
        for rule in ROAD_RULES
        if (not district or district in rule.districts) and any(term in text for term in rule.terms)
    ]
    return max(matches, key=lambda rule: rule.address_score, default=None)


def _best_station_rule(text: str) -> _StationRule | None:
    if not text:
        return None
    matches = [rule for rule in STATION_RULES if any(term in text for term in rule.terms)]
    return max(matches, key=lambda rule: rule.score, default=None)


def _effective_location(listing: Listing, address_text: str) -> tuple[str | None, str | None]:
    city = _explicit_city(address_text) or _normalize_city(listing.city)
    district = _explicit_district(address_text) or listing.district
    return city, district


def _context_location(
    city: str | None,
    district: str | None,
    context_text: str,
) -> tuple[str | None, str | None]:
    # Explicit context is useful for rejecting an impossible road/district
    # combination, but advertising copy must not override structured location.
    context_city = _explicit_city(context_text)
    context_district = _explicit_district(context_text)
    if context_city and city and context_city != city:
        return "__conflict__", "__conflict__"
    if context_district and district and context_district != district:
        return city, "__conflict__"
    return context_city or city, context_district or district


def _explicit_city(text: str) -> str | None:
    if "新北市" in text:
        return "新北市"
    if "台北市" in text:
        return "台北市"
    return None


def _explicit_district(text: str) -> str | None:
    return next((district for district in _KNOWN_DISTRICTS if district in text), None)


def _normalize_city(city: str | None) -> str | None:
    return city.replace("臺", "台") if city else None


def _has_address_detail(address_text: str) -> bool:
    return bool(re.search(r"(?:\d+|[xX])(?:巷|弄|號)", address_text))


def _route_origin(listing: Listing) -> str:
    address = listing.address or ""
    parts: list[str] = []
    city = _normalize_city(listing.city)
    if city and city not in address.replace("臺", "台"):
        parts.append(city)
    if listing.district and listing.district not in address:
        parts.append(listing.district)
    parts.append(address)
    return "".join(parts)


def _record_score_basis(
    listing: Listing,
    *,
    method: str,
    basis: str,
    confidence: str,
    reason: str,
) -> None:
    listing.raw_json["ntu_score_method"] = method
    listing.raw_json["ntu_score_basis"] = basis
    listing.raw_json["ntu_score_confidence"] = confidence
    listing.raw_json["ntu_score_reason"] = reason
