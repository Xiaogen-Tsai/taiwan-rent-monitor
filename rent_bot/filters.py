from __future__ import annotations

import re
from dataclasses import dataclass

from rent_bot.config import Settings
from rent_bot.models import Classification, Listing
from rent_bot.taiwan_591_locations import normalize_location_name


SUITE_KEYWORDS = ("套房", "獨立套房", "分租套房", "獨套", "分套", "1房1衛", "一房一衛")
EXCLUDED_KEYWORDS = (
    "雅房",
    "整層",
    "整層住家",
    "住辦",
    "車位",
    "停車位",
    "店面",
    "辦公",
    "廠房",
    "土地",
    "頂讓",
    "倉庫",
)
RENT_SUBSIDY_KEYWORDS = ("租金補貼", "租補", "可申請補助", "補助")
TAX_KEYWORDS = ("可報稅", "報稅", "可入籍", "設籍", "社會住宅", "社宅")
WASHER_KEYWORDS = ("獨立洗衣機", "室內洗衣機", "專用洗衣機", "私人洗衣機", "獨洗")
SHARED_WASHER_NEGATIVE = ("共用洗衣機", "公共洗衣機", "投幣式洗衣")
GARBAGE_COLLECTION_KEYWORDS = ("垃圾代收", "代收垃圾", "垃圾代丟", "代丟垃圾", "專人收垃圾")
GARBAGE_COLLECTION_NEGATIVE = ("無垃圾代收", "不代收垃圾", "自行倒垃圾", "垃圾自行處理")
FEMALE_ONLY_PATTERNS = (
    r"(?<!不)(?:限|只限|僅限|限租|限收|只租|僅租|限住|限入住)\s*(?:單身)?\s*(?:女生|女性|女學生|女上班族|女房客|女租客|女)\s*(?:入住|租住|承租|租客|房客)?",
    r"(?:女生|女性|女學生|女上班族|女房客|女租客|女)\s*(?:限定|限住|限租|入住|租住|承租|專屬|優先|佳)",
    r"\[(?:女生|女性|女學生|女上班族|女房客|女租客|女)\s*/",
)
FEMALE_ONLY_GENDER_VALUES = {"女", "女性", "女生", "female", "f"}
GENDER_NEUTRAL_MARKERS = ("不限", "不拘", "皆可", "男女", "男/女", "男、女", "male/female", "any")
GENDER_RESTRICTION_KEY_PATTERN = re.compile(
    r"(?:gender|sex|性別|租客|房客|tenant|renter|roomer|身分|身份|條件|condition|入住|租住)",
    re.I,
)


@dataclass
class FilterResult:
    ok: bool
    reasons: list[str]


def keyword_classify(listing: Listing) -> Classification:
    text = listing.text_for_classification()
    red_flags = [word for word in EXCLUDED_KEYWORDS if word in text]
    if listing_has_female_only_restriction(listing):
        red_flags.append("限女")
    is_suite = any(word in text for word in SUITE_KEYWORDS) and not any(
        word in text for word in EXCLUDED_KEYWORDS
    )
    has_washer: bool | None = None
    if any(word in text for word in WASHER_KEYWORDS):
        has_washer = True
    elif any(word in text for word in SHARED_WASHER_NEGATIVE):
        has_washer = False
    has_garbage_collection: bool | None = None
    if any(word in text for word in GARBAGE_COLLECTION_NEGATIVE):
        has_garbage_collection = False
    elif any(word in text for word in GARBAGE_COLLECTION_KEYWORDS):
        has_garbage_collection = True

    has_subsidy: bool | None = True if any(word in text for word in RENT_SUBSIDY_KEYWORDS) else None
    has_tax: bool | None = True if any(word in text for word in TAX_KEYWORDS) else None
    if "不可報稅" in text or "不能報稅" in text:
        has_tax = False
    if "不可租補" in text or "不能租補" in text or "不租補" in text:
        has_subsidy = False

    return Classification(
        is_suite=is_suite,
        has_rent_subsidy=has_subsidy,
        has_tax_registration=has_tax,
        has_independent_washer=has_washer,
        has_garbage_collection=has_garbage_collection,
        red_flags=red_flags,
        summary=_short_summary(text),
        score_reason="keyword fallback",
    )


def apply_classification(listing: Listing, classification: Classification) -> Listing:
    if listing.has_rent_subsidy is None:
        listing.has_rent_subsidy = classification.has_rent_subsidy
    if listing.has_tax_registration is None:
        listing.has_tax_registration = classification.has_tax_registration
    if listing.has_independent_washer is None:
        listing.has_independent_washer = classification.has_independent_washer
    if listing.has_garbage_collection is None:
        listing.has_garbage_collection = classification.has_garbage_collection
    if not listing.room_type and classification.is_suite:
        listing.room_type = "套房"
    if classification.red_flags:
        for flag in classification.red_flags:
            tag = f"red_flag:{flag}"
            if tag not in listing.tags:
                listing.tags.append(tag)
    if classification.summary and "classifier_summary" not in listing.raw_json:
        listing.raw_json["classifier_summary"] = classification.summary
    if classification.score_reason:
        listing.raw_json["classifier_score_reason"] = classification.score_reason
    return listing


def listing_matches(listing: Listing, settings: Settings) -> FilterResult:
    reasons: list[str] = []
    city = normalize_location_name(listing.city) if listing.city else None
    district = normalize_location_name(listing.district) if listing.district else None
    allowed_districts = settings.allowed_city_districts.get(city or "", set())
    if not city or not district or ("*" not in allowed_districts and district not in allowed_districts):
        reasons.append(f"地區不符: {city or '未知'} {district or '未知'}")

    text = listing.text_for_classification()
    if any(word in text for word in EXCLUDED_KEYWORDS):
        reasons.append("排除房型/用途")
    if settings.exclude_female_only and listing_has_female_only_restriction(listing):
        reasons.append("限女/女性限定")
    if listing.room_type and "雅房" in listing.room_type:
        reasons.append("雅房")
    if settings.suite_only and "套房" not in (listing.room_type or "") and not any(
        word in text for word in SUITE_KEYWORDS
    ):
        reasons.append("不是明確套房")

    cost = listing.monthly_cost_for_filter()
    if cost is None:
        reasons.append("租金未知")
    elif cost > settings.max_rent:
        reasons.append(f"租金超過 {settings.max_rent}: {cost}")

    if listing.area_ping is None:
        reasons.append("坪數未知")
    elif listing.area_ping < settings.min_area_ping:
        reasons.append(f"坪數小於 {settings.min_area_ping}: {listing.area_ping}")

    return FilterResult(ok=not reasons, reasons=reasons)


def extract_first_int(text: str) -> int | None:
    match = re.search(r"(\d[\d,]*)", text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def has_female_only_restriction(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text)
    return any(re.search(pattern, normalized) for pattern in FEMALE_ONLY_PATTERNS)


def listing_has_female_only_restriction(listing: Listing) -> bool:
    return has_female_only_restriction(listing.text_for_classification()) or _raw_json_has_female_only_gender_field(
        listing.raw_json
    )


def _raw_json_has_female_only_gender_field(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if _looks_like_gender_restriction_key(str(key)) and _gender_field_is_female_only(child):
                return True
            if isinstance(child, (dict, list)) and _raw_json_has_female_only_gender_field(child):
                return True
    elif isinstance(value, list):
        return any(_raw_json_has_female_only_gender_field(item) for item in value)
    return False


def _looks_like_gender_restriction_key(key: str) -> bool:
    return bool(GENDER_RESTRICTION_KEY_PATTERN.search(key))


def _gender_field_is_female_only(value: object) -> bool:
    return any(_gender_text_is_female_only(text) for text in _flatten_gender_text(value))


def _gender_text_is_female_only(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text).lower()
    if not normalized:
        return False
    if any(marker in normalized for marker in GENDER_NEUTRAL_MARKERS):
        return False
    if normalized in FEMALE_ONLY_GENDER_VALUES:
        return True
    return has_female_only_restriction(text)


def _flatten_gender_text(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(_flatten_gender_text(child))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_gender_text(item))
        return result
    return []


def _short_summary(text: str, limit: int = 160) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[:limit]
