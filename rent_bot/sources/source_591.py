from __future__ import annotations

import ast
import html as html_lib
import json
import logging
import random
import re
import time
from collections.abc import Iterable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from bs4.element import Tag

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Page
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised only when optional runtime is missing
    PlaywrightError = Exception
    PlaywrightTimeoutError = TimeoutError
    Page = object
    sync_playwright = None

from rent_bot.models import Listing, canonical_listing_id
from rent_bot.sources.base import (
    BaseSource,
    SourceBlockedError,
    SourceError,
    SourceErrorCode,
    SourceResult,
    detect_access_wall,
)
from rent_bot.taiwan_591_locations import (
    CITY_BY_REGION_ID,
    CITY_NAMES_BY_LENGTH,
    DISTRICT_NAMES_BY_LENGTH,
    SECTION_BY_REGION_ID,
    build_591_search_urls,
    city_for_district,
    normalize_location_name,
)

logger = logging.getLogger(__name__)


PLAYWRIGHT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
DETAIL_BASE_URL = "https://rent.591.com.tw"
ROOM_TYPE_BY_KIND = {
    "2": "獨立套房",
    "3": "分租套房",
}
ROOM_KIND_BY_TYPE = {room_type: int(kind) for kind, room_type in ROOM_TYPE_BY_KIND.items()}
TITLE_KEYS = ("title", "name", "subject", "case_name", "caseName", "house_title", "post_title")
URL_KEYS = ("url", "link", "detail_url", "detailUrl", "house_url", "share_url", "shareUrl")
ID_KEYS = ("id", "houseid", "houseId", "house_id", "post_id", "postid", "case_id", "caseId")
PRICE_KEYS = ("price", "rent", "rent_price", "rentPrice", "price_total", "priceTotal", "money")
FEE_KEYS = ("manage_fee", "manageFee", "management_fee", "managementFee", "service_fee", "extra_fee")
AREA_KEYS = ("area", "area_ping", "ping", "坪數")
ROOM_KEYS = ("kind_name", "kindName", "room_type", "roomType", "type_name", "shape_name", "layout")
CITY_KEYS = ("region_name", "regionName", "city", "county", "addressRegion")
DISTRICT_KEYS = ("section_name", "sectionName", "district", "addressLocality")
ADDRESS_KEYS = ("address", "addr", "street_name", "streetName", "streetAddress", "location")
DESCRIPTION_KEYS = ("description", "desc", "content", "remark", "house_desc", "houseDesc")
IMAGE_KEYS = ("image", "images", "img", "cover", "photo", "photos", "photo_list", "photoList", "filename")
TAG_KEYS = ("tags", "tag", "label", "labels", "feature", "features")
GENDER_RESTRICTION_KEY_PATTERN = re.compile(
    r"(?:gender|sex|性別|租客|房客|tenant|renter|roomer|身分|身份|條件|condition|入住|租住)",
    re.I,
)


class Source591(BaseSource):
    name = "591"

    def fetch(self) -> SourceResult:
        if not self.settings.source_591_enabled:
            return SourceResult(
                listings=[],
                errors=[
                    "591 source disabled. Automated collection may violate site terms unless you have permission."
                ],
            )
        try:
            search_urls = self._search_urls()
        except ValueError as exc:
            return SourceResult(listings=[], errors=[str(exc)])
        if not search_urls:
            return SourceResult(
                listings=[],
                errors=["No 591 locations configured. Add [locations] or advanced search_urls."],
            )
        if sync_playwright is None:
            return SourceResult(
                listings=[],
                errors=[
                    SourceError(
                        SourceErrorCode.PARSE_ERROR,
                        "playwright is not installed; run `python -m pip install -r requirements.txt` "
                        "and `python -m playwright install chromium`",
                    )
                ],
            )

        listings: list[Listing] = []
        seen_listing_keys: set[tuple[str, str]] = set()
        errors: list[SourceError] = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        user_agent=PLAYWRIGHT_USER_AGENT,
                        viewport={"width": 1920, "height": 1080},
                        locale="zh-TW",
                        timezone_id="Asia/Taipei",
                        java_script_enabled=True,
                        extra_http_headers={"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
                    )
                    context.set_default_timeout(self.settings.http_timeout_seconds * 1000)
                    try:
                        request_index = 0
                        stopped = False
                        for base_url in _expand_section_search_urls(search_urls):
                            no_new_pages = 0
                            for page_index, url in enumerate(
                                _paginated_search_urls(
                                    base_url,
                                    self.settings.source_591_max_pages,
                                    self.settings.source_591_page_size,
                                )
                            ):
                                if request_index > 0:
                                    self._sleep_between_requests()
                                request_index += 1
                                page = context.new_page()
                                try:
                                    html = self._fetch_rendered_html(page, url)
                                    parsed = self._parse_public_html(url, html)
                                    if not parsed:
                                        if page_index == 0:
                                            errors.append(
                                                SourceError(
                                                    SourceErrorCode.EMPTY_RESULT,
                                                    "no listings parsed from rendered HTML",
                                                    url,
                                                )
                                            )
                                        break

                                    recent = _filter_recent_listings(parsed, self.settings.source_591_max_age_days)
                                    new_recent: list[Listing] = []
                                    for listing in recent:
                                        key = (listing.source, listing.listing_id)
                                        if key in seen_listing_keys:
                                            continue
                                        seen_listing_keys.add(key)
                                        new_recent.append(listing)
                                    listings.extend(new_recent)
                                    logger.info(
                                        "591 page parsed: page=%s parsed=%s recent=%s new=%s url=%s",
                                        page_index + 1,
                                        len(parsed),
                                        len(recent),
                                        len(new_recent),
                                        url,
                                    )
                                    if new_recent:
                                        no_new_pages = 0
                                    else:
                                        no_new_pages += 1
                                    if no_new_pages >= 2:
                                        break
                                except SourceBlockedError as exc:
                                    errors.append(SourceError(exc.code, exc.message, exc.url))
                                    stopped = True
                                    break
                                except PlaywrightTimeoutError as exc:
                                    logger.warning("591 Playwright timeout for %s: %s", url, exc)
                                    errors.append(SourceError(SourceErrorCode.PARSE_ERROR, f"playwright timeout: {exc}", url))
                                    stopped = True
                                    break
                                except PlaywrightError as exc:
                                    logger.warning("591 Playwright error for %s: %s", url, exc)
                                    errors.append(SourceError(SourceErrorCode.PARSE_ERROR, f"playwright error: {exc}", url))
                                    stopped = True
                                    break
                                except Exception as exc:  # noqa: BLE001
                                    logger.exception("591 source failed for %s", url)
                                    errors.append(SourceError(SourceErrorCode.PARSE_ERROR, str(exc), url))
                                    stopped = True
                                    break
                                finally:
                                    page.close()
                            if stopped:
                                break
                    finally:
                        context.close()
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001
            logger.exception("591 Playwright runtime failed")
            errors.append(SourceError(SourceErrorCode.PARSE_ERROR, f"playwright runtime failed: {exc}"))
        return SourceResult(listings=_dedupe(listings), errors=errors)

    def _search_urls(self) -> list[str]:
        if self.settings.source_591_search_urls:
            return list(self.settings.source_591_search_urls)
        kinds = [ROOM_KIND_BY_TYPE[room_type] for room_type in self.settings.source_591_room_types]
        return build_591_search_urls(self.settings.allowed_city_districts, kinds=kinds)

    def _fetch_rendered_html(self, page: Page, url: str) -> str:
        blocked_statuses: list[int] = []

        def record_blocked_response(response) -> None:  # noqa: ANN001
            if "rent.591.com.tw" in response.url and response.status in {401, 403, 429}:
                blocked_statuses.append(response.status)

        page.on("response", record_blocked_response)
        logger.info("591 opening with Playwright: %s", url)
        response = page.goto(url, wait_until="domcontentloaded", timeout=self.settings.http_timeout_seconds * 1000)
        if response is not None and response.status in {401, 403, 429}:
            raise _blocked_http_error(response.status, url)

        _random_sleep(3.0, 6.0)
        try:
            page.wait_for_load_state("networkidle", timeout=min(self.settings.http_timeout_seconds * 1000, 10_000))
        except PlaywrightTimeoutError:
            logger.info("591 networkidle wait timed out; continuing with rendered DOM for %s", url)

        self._light_scroll(page)
        html = page.content()
        if blocked_statuses:
            status = 429 if 429 in blocked_statuses else 403 if 403 in blocked_statuses else 401
            raise _blocked_http_error(status, url)
        access_wall = _detect_access_wall(html)
        if access_wall is not None:
            logger.warning("591 access wall detected after render for %s: %s", url, access_wall.value)
            raise SourceBlockedError(access_wall, access_wall.value, url)
        return html

    def _sleep_between_requests(self) -> None:
        _random_sleep(self.settings.request_min_delay_seconds, self.settings.request_max_delay_seconds)

    def _light_scroll(self, page: Page) -> None:
        for _ in range(3):
            page.mouse.wheel(0, random.randint(450, 900))
            _random_sleep(0.8, 1.8)
        page.evaluate("window.scrollTo({ top: 0, behavior: 'instant' })")

    def _parse_public_html(self, page_url: str, html: str) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[Listing] = []

        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.get_text(strip=True))
            except json.JSONDecodeError:
                continue
            for item in _walk_json_objects(data):
                listing = _listing_from_jsonld(self.name, page_url, item)
                if listing is not None:
                    listings.append(listing)

        listings.extend(_listings_from_embedded_state(self.name, page_url, soup))
        listings.extend(_listings_from_html_cards(self.name, page_url, soup))
        return _dedupe(listings)


def _walk_json_objects(value: object) -> list[dict]:
    found: list[dict] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_walk_json_objects(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_json_objects(child))
    return found


def _listing_from_jsonld(source: str, page_url: str, data: dict) -> Listing | None:
    name = str(data.get("name") or data.get("title") or "")
    url = str(data.get("url") or "")
    description = str(data.get("description") or "")
    if not name and not description:
        return None
    if "套房" not in f"{name}\n{description}":
        return None
    listing_url = urljoin(page_url, url) if url else page_url
    offers = data.get("offers") if isinstance(data.get("offers"), dict) else {}
    price = offers.get("price") if offers else data.get("price")
    rent = _safe_int(price)
    address = data.get("address")
    address_text = ""
    if isinstance(address, dict):
        address_text = " ".join(str(address.get(key) or "") for key in ("addressRegion", "addressLocality", "streetAddress")).strip()
    elif address:
        address_text = str(address)
    city, district = _extract_location(address_text + "\n" + name + "\n" + description)
    image_urls = _extract_json_images(data)
    text = name + "\n" + description
    return Listing(
        source=source,
        listing_id=_extract_591_id(listing_url) or canonical_listing_id(source, listing_url),
        url=listing_url,
        title=name[:120] or "591 房源",
        city=city,
        district=district,
        address=address_text or None,
        rent=rent,
        total_monthly_cost=rent,
        area_ping=_extract_area(text),
        room_type=_extract_room_type(text),
        description=description[:4000],
        image_urls=image_urls[:3],
        raw_json={"jsonld": data, "update_age_days": _extract_update_age_days(text)},
        status="active",
    )


def _listings_from_embedded_state(source: str, page_url: str, soup: BeautifulSoup) -> list[Listing]:
    listings: list[Listing] = []
    for script in soup.select("script"):
        script_type = (script.get("type") or "").lower()
        if script_type == "application/ld+json":
            continue
        text = script.string or script.get_text()
        if not text or "591" not in text and "rent" not in text and "套房" not in text:
            continue
        for value in _script_json_values(text, script_type):
            for item in _walk_json_objects(value):
                listing = _listing_from_591_json(source, page_url, item)
                if listing is not None:
                    listings.append(listing)
    return listings


def _script_json_values(text: str, script_type: str = "") -> Iterable[object]:
    text = html_lib.unescape(text).strip()
    if not text:
        return []

    values: list[object] = []
    if "json" in script_type or text[:1] in {"{", "["}:
        value = _try_json_loads(text)
        if value is not None:
            values.append(value)

    for match in re.finditer(r"JSON\.parse\(\s*('(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")\s*\)", text):
        try:
            decoded = ast.literal_eval(match.group(1))
        except (SyntaxError, ValueError):
            continue
        value = _try_json_loads(decoded)
        if value is not None:
            values.append(value)

    assignment_pattern = re.compile(
        r"(?:window\.)?(?:__INITIAL_STATE__|__NEXT_DATA__|__NUXT__|INITIAL_STATE|APP_STATE|state)\s*=\s*"
    )
    for match in assignment_pattern.finditer(text):
        candidate = _extract_balanced_json_value(text, match.end())
        if not candidate:
            continue
        value = _try_json_loads(candidate)
        if value is not None:
            values.append(value)
    return values


def _try_json_loads(value: str) -> object | None:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _extract_balanced_json_value(text: str, start: int) -> str | None:
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text) or text[start] not in "{[":
        return None

    pairs = {"{": "}", "[": "]"}
    stack = [pairs[text[start]]]
    in_string = False
    quote = ""
    escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
            continue
        if char in pairs:
            stack.append(pairs[char])
            continue
        if stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return text[start : index + 1]
    return None


def _listing_from_591_json(source: str, page_url: str, data: dict) -> Listing | None:
    listing_id = _extract_json_listing_id(data)
    listing_url = _extract_json_url(page_url, data, listing_id)
    text = _json_listing_text(data)
    room_type = _extract_room_type(_first_string(data, ROOM_KEYS) + "\n" + text)
    room_type = room_type or _room_type_from_page_url(page_url)

    if listing_id is None and not listing_url:
        return None
    if not text.strip():
        return None
    if not (room_type or "套房" in text):
        return None
    if not _has_listing_signal(data, text):
        return None

    city, district = _extract_json_location(page_url, data, text)
    address = _first_string(data, ADDRESS_KEYS)
    rent = _extract_json_price(data) or _extract_rent(text)
    management_fee = _extract_management_fee(data, text)
    total_monthly_cost = rent + management_fee if rent is not None and management_fee is not None else rent
    title = _first_string(data, TITLE_KEYS) or _short_title(text)
    image_urls = _extract_json_images(data)
    tags = _extract_json_tags(data)
    gender_restriction = _extract_json_gender_restriction(data)
    raw_json = {
        "parser": "embedded_state",
        "page_url": page_url,
        "management_fee": management_fee,
        "update_age_days": _extract_update_age_days(text),
    }
    if gender_restriction:
        raw_json["gender_restriction"] = gender_restriction
    return Listing(
        source=source,
        listing_id=listing_id or canonical_listing_id(source, listing_url or page_url + title),
        url=listing_url or _detail_url_from_id(listing_id),
        title=title[:120] or "591 房源",
        city=city,
        district=district,
        address=address or None,
        rent=rent,
        total_monthly_cost=total_monthly_cost,
        area_ping=_extract_json_area(data) or _extract_area(text),
        room_type=room_type,
        floor=_extract_floor(data),
        description=text[:4000],
        tags=tags,
        image_urls=image_urls[:3],
        raw_json=raw_json,
        status="active",
    )


def _has_listing_signal(data: dict, text: str) -> bool:
    has_title = bool(_first_string(data, TITLE_KEYS))
    has_price = _extract_json_price(data) is not None or _extract_rent(text) is not None
    has_location = bool(_first_string(data, CITY_KEYS + DISTRICT_KEYS + ADDRESS_KEYS))
    return has_title and (has_price or has_location)


def _extract_json_listing_id(data: dict) -> str | None:
    for key in ID_KEYS:
        value = data.get(key)
        if value is None:
            continue
        match = re.search(r"\d{4,}", str(value))
        if match:
            return match.group(0)
    for key in URL_KEYS:
        value = data.get(key)
        if value:
            listing_id = _extract_591_id(str(value))
            if listing_id:
                return listing_id
    return None


def _extract_json_url(page_url: str, data: dict, listing_id: str | None) -> str | None:
    for key in URL_KEYS:
        value = data.get(key)
        if not value:
            continue
        url = _normalize_url(page_url, str(value))
        if _looks_like_591_detail_url(url):
            return url
    return _detail_url_from_id(listing_id) if listing_id else None


def _detail_url_from_id(listing_id: str | None) -> str | None:
    if not listing_id:
        return None
    return f"{DETAIL_BASE_URL}/rent-detail-{listing_id}.html"


def _json_listing_text(data: dict) -> str:
    pieces = [_first_string(data, TITLE_KEYS), _first_string(data, DESCRIPTION_KEYS)]
    for key in ADDRESS_KEYS + ROOM_KEYS + TAG_KEYS:
        pieces.extend(_flatten_text(data.get(key)))
    gender_restriction = _extract_json_gender_restriction(data)
    if gender_restriction:
        pieces.append(gender_restriction)
    return "\n".join(piece for piece in pieces if piece)


def _first_string(data: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _flatten_text(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        return [
            item
            for key in ("name", "text", "label", "title", "value")
            for item in _flatten_text(value.get(key))
        ]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_text(item))
        return result
    return []


def _extract_json_gender_restriction(data: dict) -> str | None:
    values = _extract_gender_restriction_values(data)
    deduped = list(dict.fromkeys(item for item in values if item))
    return " / ".join(deduped)[:200] if deduped else None


def _extract_gender_restriction_values(value: object) -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for key, child in value.items():
            if GENDER_RESTRICTION_KEY_PATTERN.search(str(key)):
                result.extend(_flatten_all_text(child))
            result.extend(_extract_gender_restriction_values(child))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_extract_gender_restriction_values(item))
        return result
    return []


def _flatten_all_text(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(_flatten_all_text(child))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_all_text(item))
        return result
    return []


def _extract_json_location(page_url: str, data: dict, text: str) -> tuple[str | None, str | None]:
    district = _first_string(data, DISTRICT_KEYS)
    city = _first_string(data, CITY_KEYS)
    page_city, _page_district = _location_from_page_url(page_url)
    city = normalize_location_name(city) if city else None
    if district:
        district = normalize_location_name(district)
        resolved_city = city_for_district(district, preferred_city=city or page_city)
        return resolved_city or city or page_city, district
    if city:
        _found_city, found_district = _extract_location(text, page_url=page_url)
        return city, found_district
    return _extract_location(text, page_url=page_url)


def _extract_json_price(data: dict) -> int | None:
    for key in PRICE_KEYS:
        value = data.get(key)
        parsed = _safe_int(value)
        if parsed is not None:
            return parsed
    return None


def _extract_management_fee(data: dict, text: str) -> int | None:
    for key in FEE_KEYS:
        parsed = _safe_int(data.get(key))
        if parsed is not None and parsed <= 10_000:
            return parsed
    patterns = [
        r"(?:管理費|管費)\D{0,8}(\d[\d,]{1,5})",
        r"(\d[\d,]{1,5})\s*(?:元)?\s*(?:管理費|管費)",
    ]
    fees: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = int(match.group(1).replace(",", ""))
            if 0 <= value <= 10_000:
                fees.append(value)
    return max(fees) if fees else None


def _extract_json_area(data: dict) -> float | None:
    for key in AREA_KEYS:
        parsed = _safe_float(data.get(key))
        if parsed is not None:
            return parsed
    return None


def _extract_floor(data: dict) -> str | None:
    floor = _first_string(data, ("floor", "floor_str", "floorStr"))
    all_floor = _first_string(data, ("allfloor", "all_floor", "allFloor"))
    if floor and all_floor and "/" not in floor:
        return f"{floor}/{all_floor}F"
    return floor or None


def _extract_floor_from_text(text: str) -> str | None:
    match = re.search(
        r"(?<!\w)((?:B\d+|地下\d+|頂樓加蓋|\d+)\s*F?\s*/\s*(?:B?\d+|地下\d+|\d+)\s*F)",
        text,
        re.I,
    )
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(1)).upper()


def _extract_json_tags(data: dict) -> list[str]:
    tags: list[str] = []
    for key in TAG_KEYS:
        for item in _flatten_text(data.get(key)):
            if item and item not in tags:
                tags.append(item)
    return tags[:12]


def _listings_from_html_cards(source: str, page_url: str, soup: BeautifulSoup) -> list[Listing]:
    listings: list[Listing] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        listing_url = _normalize_url(page_url, href)
        if not _looks_like_591_detail_url(listing_url):
            continue
        text = _card_text(anchor)
        if not text or "套房" not in text and _room_type_from_page_url(page_url) is None:
            continue
        listing_id = _extract_591_id(listing_url) or canonical_listing_id(source, listing_url)
        city, district = _extract_location(text, page_url=page_url)
        rent = _extract_rent(text)
        room_type = _extract_room_type(text) or _room_type_from_page_url(page_url)
        title = _short_title(text)
        update_age_days = _extract_update_age_days(text)
        listings.append(
            Listing(
                source=source,
                listing_id=listing_id,
                url=listing_url,
                title=title[:120] or "591 房源",
                city=city,
                district=district,
                address=_extract_address_from_text(text, city=city, district=district),
                rent=rent,
                total_monthly_cost=rent,
                area_ping=_extract_area(text),
                room_type=room_type,
                floor=_extract_floor_from_text(text),
                description=text[:4000],
                image_urls=_extract_card_images(anchor, page_url, listing_url)[:3],
                raw_json={"parser": "anchor_card", "page_url": page_url, "update_age_days": update_age_days},
                status="active",
            )
        )
    return listings


def _card_text(anchor: Tag) -> str:
    candidates = [anchor]
    parent = anchor.parent
    for _ in range(4):
        if not isinstance(parent, Tag):
            break
        candidates.append(parent)
        parent = parent.parent
    texts = [node.get_text(" ", strip=True) for node in candidates]
    texts = [text for text in texts if text]
    if not texts:
        return ""
    return min(texts, key=lambda value: abs(len(value) - 260))[:4000]


def _extract_card_images(anchor: Tag, page_url: str | None = None, listing_url: str | None = None) -> list[str]:
    for node in _card_image_candidates(anchor, page_url, listing_url):
        urls = _extract_node_images(node)
        if urls:
            return urls
    return []


def _card_image_candidates(anchor: Tag, page_url: str | None, listing_url: str | None) -> list[Tag]:
    candidates = [anchor]
    parent = anchor.parent
    for _ in range(4):
        if not isinstance(parent, Tag):
            break
        if _candidate_matches_listing(parent, page_url, listing_url):
            candidates.append(parent)
        parent = parent.parent
    return candidates


def _candidate_matches_listing(node: Tag, page_url: str | None, listing_url: str | None) -> bool:
    if not page_url or not listing_url:
        return True
    listing_id = _extract_591_id(listing_url)
    other_listing_ids: set[str] = set()
    for link in node.select("a[href]"):
        candidate_url = _normalize_url(page_url, str(link.get("href", "")))
        if not _looks_like_591_detail_url(candidate_url):
            continue
        candidate_id = _extract_591_id(candidate_url)
        if candidate_id:
            other_listing_ids.add(candidate_id)
    return not other_listing_ids or other_listing_ids == {listing_id}


def _extract_node_images(node: Tag) -> list[str]:
    urls: list[str] = []
    for image in node.select("img, source"):
        for value in _image_attribute_values(image):
            _append_image_url(urls, value)
    for element in node.select("[style]"):
        for value in _style_image_values(str(element.get("style") or "")):
            _append_image_url(urls, value)
    return urls


def _image_attribute_values(image: Tag) -> Iterable[str]:
    for attr in (
        "src",
        "data-src",
        "data-original",
        "data-lazy",
        "data-url",
        "data-img",
        "data-original-src",
        "srcset",
        "data-srcset",
    ):
        value = image.get(attr)
        if not value:
            continue
        if attr.endswith("srcset"):
            yield from _srcset_image_values(str(value))
        else:
            yield str(value)


def _srcset_image_values(value: str) -> Iterable[str]:
    for item in value.split(","):
        url = item.strip().split(maxsplit=1)[0]
        if url:
            yield url


def _style_image_values(value: str) -> Iterable[str]:
    for match in re.finditer(r"url\(([^)]+)\)", html_lib.unescape(value)):
        yield match.group(1).strip(" '\"")


def _append_image_url(urls: list[str], value: str) -> None:
    url = _normalize_image_url(value)
    if url and url not in urls:
        urls.append(url)


def _looks_like_591_detail_url(url: str) -> bool:
    return bool(re.search(r"(?:rent-detail-\d+\.html|/home/\d+)", url)) or _numeric_detail_id(url) is not None


def _blocked_http_error(status: int, url: str) -> SourceBlockedError:
    if status == 429:
        return SourceBlockedError(SourceErrorCode.HTTP_RATE_LIMITED, "HTTP 429", url)
    if status == 401:
        return SourceBlockedError(SourceErrorCode.LOGIN_REQUIRED, "HTTP 401", url)
    return SourceBlockedError(SourceErrorCode.HTTP_FORBIDDEN, f"HTTP {status}", url)


def _paginated_search_urls(base_url: str, max_pages: int, page_size: int) -> list[str]:
    urls: list[str] = []
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query.pop("firstRow", None)
    for page_index in range(max_pages):
        page_query = {key: values[:] for key, values in query.items()}
        if page_index > 0:
            page_query["firstRow"] = [str(page_index * page_size)]
        encoded = urlencode(page_query, doseq=True)
        urls.append(urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, encoded, parsed.fragment)))
    return urls


def _expand_section_search_urls(search_urls: Iterable[str]) -> list[str]:
    expanded: list[str] = []
    for search_url in search_urls:
        parsed = urlparse(search_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        section = query.get("section", [""])[0]
        section_tokens = [token.strip() for token in re.split(r"[-,]", section) if token.strip()]
        if len(section_tokens) <= 1:
            expanded.append(search_url)
            continue
        for section_token in section_tokens:
            section_query = {key: values[:] for key, values in query.items()}
            section_query["section"] = [section_token]
            section_query.pop("firstRow", None)
            encoded = urlencode(section_query, doseq=True)
            expanded.append(
                urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, encoded, parsed.fragment))
            )
    return expanded


def _filter_recent_listings(listings: list[Listing], max_age_days: int) -> list[Listing]:
    recent: list[Listing] = []
    for listing in listings:
        age = listing.raw_json.get("update_age_days")
        if isinstance(age, int) and age > max_age_days:
            continue
        recent.append(listing)
    return recent


def _extract_update_age_days(text: str) -> int | None:
    if any(marker in text for marker in ("新上架", "剛剛更新", "今天更新", "今日更新")):
        return 0
    if re.search(r"\d+\s*(?:分鐘|小時)(?:內|前)?更新", text):
        return 0
    match = re.search(r"(\d+)\s*天前更新", text)
    if match:
        return int(match.group(1))
    if "昨日更新" in text or "昨天更新" in text:
        return 1
    return None


def _random_sleep(min_seconds: float, max_seconds: float) -> None:
    if max_seconds < min_seconds:
        max_seconds = min_seconds
    delay = random.uniform(min_seconds, max_seconds)
    if delay > 0:
        time.sleep(delay)


def _normalize_url(page_url: str, value: str) -> str:
    value = value.strip()
    if value.startswith("//"):
        return "https:" + value
    return urljoin(page_url, value)


def _extract_591_id(url: str) -> str | None:
    patterns = [r"/rent-detail-(\d+)\.html", r"/home/(\d+)", r"[?&]id=(\d+)"]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return _numeric_detail_id(url)


def _numeric_detail_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc and not parsed.netloc.endswith("rent.591.com.tw"):
        return None
    match = re.fullmatch(r"/?(\d{4,})/?", parsed.path)
    return match.group(1) if match else None


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d[\d,]*", str(value))
    if not match:
        return None
    parsed = int(match.group(0).replace(",", ""))
    return parsed if 1_000 <= parsed <= 200_000 else None


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    parsed = float(match.group(0))
    return parsed if 1 <= parsed <= 100 else None


def _extract_rent(text: str) -> int | None:
    patterns = [
        r"(?:租金|月租|價格|房租|租)\D{0,12}(\d[\d,]{3,6})",
        r"(\d[\d,]{3,6})\s*(?:元/月|元|/月|每月)",
    ]
    values: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = int(match.group(1).replace(",", ""))
            if 3_000 <= value <= 200_000:
                values.append(value)
    return max(values) if values else None


def _extract_area(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*坪", text)
    return float(match.group(1)) if match else None


def _extract_room_type(text: str) -> str | None:
    if "獨立套房" in text:
        return "獨立套房"
    if "分租套房" in text:
        return "分租套房"
    if "獨套" in text:
        return "獨立套房"
    if "分套" in text:
        return "分租套房"
    if "套房" in text:
        return "套房"
    return None


def _extract_location(text: str, page_url: str | None = None) -> tuple[str | None, str | None]:
    normalized_text = normalize_location_name(text)
    page_city, page_district = _location_from_page_url(page_url) if page_url else (None, None)
    city = next((item for item in CITY_NAMES_BY_LENGTH if item in normalized_text), None)
    preferred_city = city or page_city
    for district in DISTRICT_NAMES_BY_LENGTH:
        if district not in normalized_text:
            continue
        district_city = city_for_district(district, preferred_city=preferred_city)
        if district_city:
            return district_city, district
    if page_url:
        return city or page_city, page_district
    return city, None


def _location_from_page_url(page_url: str) -> tuple[str | None, str | None]:
    query = parse_qs(urlparse(page_url).query)
    region_id = _first_query_token(query.get("region"))
    section_id = _single_section_token(query.get("section"))
    city = CITY_BY_REGION_ID.get(region_id or "")
    district = None
    if region_id and section_id:
        district = SECTION_BY_REGION_ID.get(region_id, {}).get(section_id)
    return city, district


def _first_query_token(values: list[str] | None) -> str | None:
    if not values:
        return None
    return values[0].split("-")[0].strip() or None


def _single_section_token(values: list[str] | None) -> str | None:
    if not values:
        return None
    raw = values[0].strip()
    if not raw or "-" in raw:
        return None
    return raw


def _extract_json_images(data: dict) -> list[str]:
    urls: list[str] = []
    for key in IMAGE_KEYS:
        for url in _flatten_image_urls(data.get(key)):
            if url and url not in urls:
                urls.append(url)
    return urls


def _flatten_image_urls(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        url = _normalize_image_url(value)
        return [url] if url else []
    if isinstance(value, dict):
        urls: list[str] = []
        for key in ("url", "src", "path", "image", "filename"):
            urls.extend(_flatten_image_urls(value.get(key)))
        return urls
    if isinstance(value, list):
        urls: list[str] = []
        for item in value:
            urls.extend(_flatten_image_urls(item))
        return urls
    return []


def _normalize_image_url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/"):
        return urljoin(DETAIL_BASE_URL, value)
    return None


def _room_type_from_page_url(page_url: str) -> str | None:
    query = parse_qs(urlparse(page_url).query)
    kind = _first_query_token(query.get("kind"))
    return ROOM_TYPE_BY_KIND.get(kind or "")


def _short_title(text: str) -> str:
    for line in re.split(r"[\n。]", text):
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) >= 4:
            return line[:120]
    return re.sub(r"\s+", " ", text).strip()[:120]


def _extract_address_from_text(
    text: str,
    *,
    city: str | None = None,
    district: str | None = None,
) -> str | None:
    normalized_text = normalize_location_name(text)
    if not district:
        city, district = _extract_location(normalized_text)
    if not district:
        return None
    city = normalize_location_name(city) if city else None
    district = normalize_location_name(district)
    city_prefix = rf"(?:{re.escape(city)})?" if city else ""
    match = re.search(
        rf"({city_prefix}{re.escape(district)}[^\s，,。]{{0,40}})",
        normalized_text,
    )
    return match.group(1) if match else None


def _detect_access_wall(html: str) -> SourceErrorCode | None:
    return detect_access_wall(html)


def _dedupe(listings: list[Listing]) -> list[Listing]:
    seen: set[tuple[str, str]] = set()
    result: list[Listing] = []
    for listing in listings:
        key = (listing.source, listing.listing_id)
        if key not in seen:
            seen.add(key)
            result.append(listing)
    return result
