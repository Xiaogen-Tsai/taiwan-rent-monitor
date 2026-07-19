from __future__ import annotations

import ast
import hashlib
import html as html_lib
import json
import logging
import random
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from bs4.element import Tag

from rent_bot.models import Listing, canonical_listing_id
from rent_bot.taiwan_591_locations import (
    DISTRICT_CITIES,
    DISTRICT_NAMES_BY_LENGTH,
    TAIWAN_591_LOCATIONS,
    city_for_district,
    normalize_location_name,
)
from rent_bot.sources.base import (
    BaseSource,
    SourceBlockedError,
    SourceError,
    SourceErrorCode,
    SourceResult,
    detect_access_wall,
)

try:
    from playwright.sync_api import BrowserContext
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Page
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised only when optional runtime is missing
    BrowserContext = object
    PlaywrightError = Exception
    PlaywrightTimeoutError = TimeoutError
    Page = object
    sync_playwright = None

logger = logging.getLogger(__name__)


PLAYWRIGHT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class PublicRentalSourceSpec:
    name: str
    enabled_attr: str
    search_urls_attr: str
    max_pages_attr: str
    max_age_days_attr: str
    detail_url_patterns: tuple[str, ...]
    page_query_param: str = "page"
    disabled_message: str = "source disabled. Enable only for low-frequency personal monitoring of public pages."
    detail_fetch_limit: int = 3


class PublicRentalSource(BaseSource):
    spec: PublicRentalSourceSpec

    @property
    def name(self) -> str:
        return self.spec.name

    def fetch(self) -> SourceResult:
        if not getattr(self.settings, self.spec.enabled_attr):
            return SourceResult(listings=[], errors=[f"{self.spec.name} {self.spec.disabled_message}"])

        search_urls = list(getattr(self.settings, self.spec.search_urls_attr))
        if not search_urls:
            return SourceResult(listings=[], errors=[f"{self.spec.search_urls_attr.upper()} is empty"])
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
        errors: list[SourceError] = []
        seen_keys: set[tuple[str, str]] = set()
        stopped = False
        max_pages = getattr(self.settings, self.spec.max_pages_attr)
        max_age_days = getattr(self.settings, self.spec.max_age_days_attr)

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
                        for search_url in search_urls:
                            for page_index, page_url in enumerate(self._page_urls(search_url, max_pages)):
                                if request_index > 0:
                                    self._sleep_between_requests()
                                request_index += 1
                                page = context.new_page()
                                try:
                                    html = self._fetch_rendered_html(context, page, page_url)
                                    parsed = self._parse_public_html(page_url, html)
                                    if not parsed:
                                        if page_index == 0:
                                            errors.append(
                                                SourceError(
                                                    SourceErrorCode.EMPTY_RESULT,
                                                    "no listings parsed from rendered HTML",
                                                    page_url,
                                                )
                                            )
                                        break
                                    parsed, detail_errors = self._enrich_missing_from_details(context, parsed)
                                    errors.extend(detail_errors)
                                    recent = _filter_recent_listings(parsed, max_age_days)
                                    new_recent: list[Listing] = []
                                    for listing in recent:
                                        key = (listing.source, listing.listing_id)
                                        if key in seen_keys:
                                            continue
                                        seen_keys.add(key)
                                        new_recent.append(listing)
                                    listings.extend(new_recent)
                                    logger.info(
                                        "%s page parsed: page=%s parsed=%s recent=%s new=%s url=%s",
                                        self.spec.name,
                                        page_index + 1,
                                        len(parsed),
                                        len(recent),
                                        len(new_recent),
                                        page_url,
                                    )
                                    if not new_recent:
                                        break
                                except SourceBlockedError as exc:
                                    errors.append(SourceError(exc.code, exc.message, exc.url))
                                    stopped = True
                                    break
                                except PlaywrightTimeoutError as exc:
                                    logger.warning("%s Playwright timeout for %s: %s", self.spec.name, page_url, exc)
                                    errors.append(SourceError(SourceErrorCode.PARSE_ERROR, f"playwright timeout: {exc}", page_url))
                                    stopped = True
                                    break
                                except PlaywrightError as exc:
                                    logger.warning("%s Playwright error for %s: %s", self.spec.name, page_url, exc)
                                    errors.append(SourceError(SourceErrorCode.PARSE_ERROR, f"playwright error: {exc}", page_url))
                                    stopped = True
                                    break
                                except Exception as exc:  # noqa: BLE001
                                    logger.exception("%s source failed for %s", self.spec.name, page_url)
                                    errors.append(SourceError(SourceErrorCode.PARSE_ERROR, str(exc), page_url))
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
            logger.exception("%s Playwright runtime failed", self.spec.name)
            errors.append(SourceError(SourceErrorCode.PARSE_ERROR, f"playwright runtime failed: {exc}"))
        return SourceResult(listings=_dedupe(listings), errors=errors)

    def _page_urls(self, base_url: str, max_pages: int) -> list[str]:
        if max_pages <= 1:
            return [base_url]
        urls = []
        parsed = urlparse(base_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query.pop(self.spec.page_query_param, None)
        for page_index in range(max_pages):
            page_query = {key: values[:] for key, values in query.items()}
            if page_index > 0:
                page_query[self.spec.page_query_param] = [str(page_index + 1)]
            encoded = urlencode(page_query, doseq=True)
            urls.append(urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, encoded, parsed.fragment)))
        return urls

    def _parse_public_html(self, page_url: str, html: str) -> list[Listing]:
        return parse_public_rental_html(self.spec, page_url, html)

    def _fetch_rendered_html(self, context: BrowserContext, page: Page, url: str) -> str:
        robots_block = self._robots_block_reason_browser(context, url)
        if robots_block is not None:
            message = (
                "robots.txt unavailable; failing closed"
                if robots_block == SourceErrorCode.ROBOTS_UNAVAILABLE
                else "robots.txt disallows fetching"
            )
            logger.warning("%s %s %s", self.spec.name, message, url)
            raise SourceBlockedError(robots_block, message, url)

        blocked_statuses: list[int] = []
        target_netloc = urlparse(url).netloc

        def record_blocked_response(response) -> None:  # noqa: ANN001
            if urlparse(response.url).netloc == target_netloc and response.status in {401, 403, 429}:
                blocked_statuses.append(response.status)

        page.on("response", record_blocked_response)
        logger.info("%s opening with Playwright: %s", self.spec.name, url)
        response = page.goto(url, wait_until="domcontentloaded", timeout=self.settings.http_timeout_seconds * 1000)
        if response is not None and response.status in {401, 403, 429}:
            raise _blocked_http_error(response.status, url)

        _random_sleep(3.0, 6.0)
        try:
            page.wait_for_load_state("networkidle", timeout=min(self.settings.http_timeout_seconds * 1000, 10_000))
        except PlaywrightTimeoutError:
            logger.info("%s networkidle wait timed out; continuing with rendered DOM for %s", self.spec.name, url)

        self._light_scroll(page)
        html = page.content()
        if blocked_statuses:
            status = 429 if 429 in blocked_statuses else 403 if 403 in blocked_statuses else 401
            raise _blocked_http_error(status, url)
        access_wall = detect_access_wall(html)
        if access_wall is not None:
            logger.warning("%s access wall detected after render for %s: %s", self.spec.name, url, access_wall.value)
            raise SourceBlockedError(access_wall, access_wall.value, url)
        return html

    def _robots_block_reason_browser(self, context: BrowserContext, url: str) -> SourceErrorCode | None:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots_cache:
            robot_url = f"{base}/robots.txt"
            self._robots_cache[base] = self._read_robots_txt_browser(context, robot_url)
        parser = self._robots_cache[base]
        if parser is False:
            return SourceErrorCode.ROBOTS_UNAVAILABLE
        if parser is None:
            return None
        if not parser.can_fetch(self.settings.user_agent, url):
            return SourceErrorCode.ROBOTS_DISALLOWED
        return None

    def _read_robots_txt_browser(self, context: BrowserContext, robot_url: str) -> RobotFileParser | bool | None:
        try:
            response = context.request.get(robot_url, timeout=self.settings.http_timeout_seconds * 1000)
        except PlaywrightError as exc:
            logger.warning("%s could not read robots.txt with Playwright %s: %s", self.spec.name, robot_url, exc)
            return False
        if response.status == 404:
            return None
        if response.status in {401, 403, 429}:
            logger.warning("%s robots.txt blocked with HTTP %s: %s", self.spec.name, response.status, robot_url)
            return False
        if response.status >= 400:
            logger.warning("%s could not read robots.txt HTTP %s: %s", self.spec.name, response.status, robot_url)
            return False

        parser = RobotFileParser()
        parser.set_url(robot_url)
        parser.parse(response.text().splitlines())
        return parser

    def _sleep_between_requests(self) -> None:
        _random_sleep(self.settings.request_min_delay_seconds, self.settings.request_max_delay_seconds)

    def _light_scroll(self, page: Page) -> None:
        for _ in range(3):
            page.mouse.wheel(0, random.randint(450, 900))
            _random_sleep(0.8, 1.8)
        page.evaluate("window.scrollTo({ top: 0, behavior: 'instant' })")

    def _enrich_missing_from_details(
        self,
        context: BrowserContext,
        listings: list[Listing],
    ) -> tuple[list[Listing], list[SourceError]]:
        enriched: list[Listing] = []
        errors: list[SourceError] = []
        fetched = 0
        seen_detail_urls: set[str] = set()
        for listing in listings:
            if fetched >= self.spec.detail_fetch_limit or not _needs_detail_enrichment(listing):
                enriched.append(listing)
                continue
            if listing.url in seen_detail_urls or not _looks_like_detail_url(listing.url, self.spec):
                enriched.append(listing)
                continue
            seen_detail_urls.add(listing.url)
            page = context.new_page()
            try:
                html = self._fetch_rendered_html(context, page, listing.url)
                detail_listings = self._parse_public_html(listing.url, html)
                if detail_listings:
                    listing = _merge_detail_listing(listing, detail_listings[0])
                fetched += 1
            except SourceBlockedError as exc:
                errors.append(SourceError(exc.code, f"detail skipped: {exc.message}", exc.url))
            except PlaywrightTimeoutError as exc:
                errors.append(SourceError(SourceErrorCode.PARSE_ERROR, f"detail skipped: playwright timeout: {exc}", listing.url))
            except PlaywrightError as exc:
                errors.append(SourceError(SourceErrorCode.PARSE_ERROR, f"detail skipped: playwright error: {exc}", listing.url))
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s detail parse failed for %s: %s", self.spec.name, listing.url, exc)
                errors.append(SourceError(SourceErrorCode.PARSE_ERROR, f"detail skipped: {exc}", listing.url))
            finally:
                page.close()
            enriched.append(listing)
        return enriched, errors


CITY_NAMES_BY_LENGTH = tuple(sorted(TAIWAN_591_LOCATIONS, key=lambda item: (-len(item), item)))

TITLE_KEYS = ("title", "name", "subject", "case_name", "caseName", "house_title", "houseTitle")
URL_KEYS = ("url", "@id", "link", "detail_url", "detailUrl", "house_url", "houseUrl", "canonical_url")
ID_KEYS = ("id", "houseid", "houseId", "house_id", "case_id", "caseId", "object_id", "objectId", "rent_id")
PRICE_KEYS = ("price", "rent", "rent_price", "rentPrice", "price_total", "priceTotal", "money", "amount")
FEE_KEYS = ("manage_fee", "manageFee", "management_fee", "managementFee", "service_fee", "extra_fee")
AREA_KEYS = ("area", "area_ping", "ping", "坪數", "buildingArea", "building_area")
ROOM_KEYS = ("kind_name", "kindName", "room_type", "roomType", "type_name", "shape_name", "layout", "use")
CITY_KEYS = ("region_name", "regionName", "city", "county", "addressRegion")
DISTRICT_KEYS = ("section_name", "sectionName", "district", "addressLocality", "town")
ADDRESS_KEYS = ("address", "addr", "street_name", "streetName", "streetAddress", "location")
DESCRIPTION_KEYS = ("description", "desc", "content", "remark", "house_desc", "houseDesc")
IMAGE_KEYS = ("image", "images", "img", "cover", "photo", "photos", "photo_list", "photoList", "filename")
TAG_KEYS = ("tags", "tag", "label", "labels", "feature", "features", "facility", "facilities")
GENDER_KEYS = ("gender", "sex", "性別", "租客", "房客", "tenant", "condition", "入住條件")

TRACKING_QUERY_KEYS = {
    "from",
    "func",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "fbclid",
    "gclid",
    "yclid",
}


def parse_public_rental_html(spec: PublicRentalSourceSpec, page_url: str, html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    listings.extend(_listings_from_jsonld(spec, page_url, soup))
    listings.extend(_listings_from_embedded_state(spec, page_url, soup))
    listings.extend(_listings_from_html_cards(spec, page_url, soup))
    if not listings and _looks_like_detail_url(page_url, spec):
        detail_listing = _listing_from_detail_page_text(spec, page_url, soup)
        if detail_listing is not None:
            listings.append(detail_listing)
    return _dedupe(listings)


def _listings_from_jsonld(spec: PublicRentalSourceSpec, page_url: str, soup: BeautifulSoup) -> list[Listing]:
    listings: list[Listing] = []
    for script in soup.select('script[type="application/ld+json"]'):
        data = _try_json_loads(script.get_text(strip=True))
        if data is None:
            continue
        for item in _walk_json_objects(data):
            listing = _listing_from_mapping(spec, page_url, item, "jsonld")
            if listing is not None:
                listings.append(listing)
    return listings


def _listings_from_embedded_state(spec: PublicRentalSourceSpec, page_url: str, soup: BeautifulSoup) -> list[Listing]:
    listings: list[Listing] = []
    for script in soup.select("script"):
        script_type = (script.get("type") or "").lower()
        if script_type == "application/ld+json":
            continue
        text = script.string or script.get_text()
        if not text or not _script_may_contain_listing(text):
            continue
        for value in _script_json_values(text, script_type):
            for item in _walk_json_objects(value):
                listing = _listing_from_mapping(spec, page_url, item, "embedded_state")
                if listing is not None:
                    listings.append(listing)
    return listings


def _script_may_contain_listing(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("rent", "house", "case", "套房", "租金", "坪", "address"))


def _blocked_http_error(status: int, url: str) -> SourceBlockedError:
    if status == 429:
        return SourceBlockedError(SourceErrorCode.HTTP_RATE_LIMITED, "HTTP 429", url)
    if status == 401:
        return SourceBlockedError(SourceErrorCode.LOGIN_REQUIRED, "HTTP 401", url)
    return SourceBlockedError(SourceErrorCode.HTTP_FORBIDDEN, f"HTTP {status}", url)


def _random_sleep(min_seconds: float, max_seconds: float) -> None:
    if max_seconds < min_seconds:
        max_seconds = min_seconds
    delay = random.uniform(min_seconds, max_seconds)
    if delay > 0:
        time.sleep(delay)


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
        r"(?:window\.)?(?:__INITIAL_STATE__|__NEXT_DATA__|__NUXT__|__APOLLO_STATE__|"
        r"INITIAL_STATE|APP_STATE|STATE|state)\s*=\s*"
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


def _listing_from_mapping(
    spec: PublicRentalSourceSpec,
    page_url: str,
    data: dict,
    parser_name: str,
) -> Listing | None:
    title = _first_string(data, TITLE_KEYS)
    explicit_url = _first_string(data, URL_KEYS)
    listing_url = canonicalize_listing_url(_normalize_url(page_url, explicit_url)) if explicit_url else ""
    listing_id = _extract_mapping_listing_id(data) or _extract_listing_id_from_url(listing_url, spec)
    text = _mapping_listing_text(data)
    room_type = _extract_room_type(_first_string(data, ROOM_KEYS) + "\n" + text)

    if not title:
        title = _short_title(text)
    if not text.strip() or not title:
        return None
    if not _has_listing_signal(spec, listing_url, data, text):
        return None

    if not listing_url:
        listing_url = canonicalize_listing_url(page_url)
    location_text = _first_string(data, ADDRESS_KEYS) + "\n" + title + "\n" + text
    city, district = _extract_location(
        location_text,
        preferred_city=_first_string(data, CITY_KEYS),
        preferred_district=_first_string(data, DISTRICT_KEYS),
    )
    address = _first_string(data, ADDRESS_KEYS) or _extract_address_from_text(location_text, city, district)
    rent = _extract_price_from_mapping(data) or _extract_rent(text)
    management_fee = _extract_management_fee(data, text)
    total_monthly_cost = rent + management_fee if rent is not None and management_fee is not None else rent
    raw_text = _clean_text(text)
    raw_json = _raw_json(
        parser_name=parser_name,
        page_url=page_url,
        canonical_url=listing_url,
        source_listing_id=listing_id,
        raw_text=raw_text,
        raw_payload=data,
        management_fee=management_fee,
    )
    return Listing(
        source=spec.name,
        listing_id=listing_id or canonical_listing_id(spec.name, listing_url + title),
        url=listing_url,
        title=title[:120],
        city=city,
        district=district,
        address=address,
        rent=rent,
        total_monthly_cost=total_monthly_cost,
        area_ping=_extract_area_from_mapping(data) or _extract_area(text),
        room_type=room_type,
        floor=_extract_floor(text),
        description=raw_text[:4000],
        tags=_extract_tags(data, text),
        has_rent_subsidy=_has_positive(text, RENT_SUBSIDY_KEYWORDS, RENT_SUBSIDY_NEGATIVE),
        has_tax_registration=_has_positive(text, TAX_KEYWORDS, TAX_NEGATIVE),
        has_independent_washer=_has_positive(text, WASHER_KEYWORDS, SHARED_WASHER_NEGATIVE),
        has_garbage_collection=_has_positive(text, GARBAGE_COLLECTION_KEYWORDS, GARBAGE_COLLECTION_NEGATIVE),
        image_urls=_extract_json_images(page_url, data)[:3],
        raw_json=raw_json,
        status="active",
    )


def _has_listing_signal(spec: PublicRentalSourceSpec, listing_url: str, data: dict, text: str) -> bool:
    has_detail_url = bool(listing_url and _looks_like_detail_url(listing_url, spec))
    has_price = _extract_price_from_mapping(data) is not None or _extract_rent(text) is not None
    has_area = _extract_area_from_mapping(data) is not None or _extract_area(text) is not None
    has_location = bool(_first_string(data, CITY_KEYS + DISTRICT_KEYS + ADDRESS_KEYS)) or _extract_location(text)[1]
    return has_detail_url and (has_price or has_area or has_location)


def _mapping_listing_text(data: dict) -> str:
    pieces = [_first_string(data, TITLE_KEYS), _first_string(data, DESCRIPTION_KEYS)]
    for key in ADDRESS_KEYS + ROOM_KEYS + TAG_KEYS + GENDER_KEYS:
        pieces.extend(_flatten_text(data.get(key)))
    if not any(pieces):
        pieces = _flatten_text(data)
    return "\n".join(piece for piece in pieces if piece)


def _listings_from_html_cards(spec: PublicRentalSourceSpec, page_url: str, soup: BeautifulSoup) -> list[Listing]:
    listings: list[Listing] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        listing_url = canonicalize_listing_url(_normalize_url(page_url, href))
        if not _looks_like_detail_url(listing_url, spec):
            continue
        text = _card_text(anchor)
        if not text.strip():
            continue
        title = _title_from_card(anchor, text)
        if not title:
            continue
        city, district = _extract_location(text)
        rent = _extract_rent(text)
        management_fee = _extract_management_fee({}, text)
        total_monthly_cost = rent + management_fee if rent is not None and management_fee is not None else rent
        listing_id = _extract_listing_id_from_url(listing_url, spec) or canonical_listing_id(spec.name, listing_url)
        raw_text = _clean_text(text)
        raw_json = _raw_json(
            parser_name="anchor_card",
            page_url=page_url,
            canonical_url=listing_url,
            source_listing_id=listing_id,
            raw_text=raw_text,
            raw_payload=None,
            management_fee=management_fee,
        )
        listings.append(
            Listing(
                source=spec.name,
                listing_id=listing_id,
                url=listing_url,
                title=title[:120],
                city=city,
                district=district,
                address=_extract_address_from_text(text, city, district),
                rent=rent,
                total_monthly_cost=total_monthly_cost,
                area_ping=_extract_area(text),
                room_type=_extract_room_type(text),
                floor=_extract_floor(text),
                description=raw_text[:4000],
                tags=_extract_tags({}, text),
                has_rent_subsidy=_has_positive(text, RENT_SUBSIDY_KEYWORDS, RENT_SUBSIDY_NEGATIVE),
                has_tax_registration=_has_positive(text, TAX_KEYWORDS, TAX_NEGATIVE),
                has_independent_washer=_has_positive(text, WASHER_KEYWORDS, SHARED_WASHER_NEGATIVE),
                has_garbage_collection=_has_positive(text, GARBAGE_COLLECTION_KEYWORDS, GARBAGE_COLLECTION_NEGATIVE),
                image_urls=_extract_card_images(anchor, page_url, listing_url)[:3],
                raw_json=raw_json,
                status="active",
            )
        )
    return listings


def _listing_from_detail_page_text(
    spec: PublicRentalSourceSpec,
    page_url: str,
    soup: BeautifulSoup,
) -> Listing | None:
    text = _clean_text(soup.get_text(" ", strip=True))
    if not text:
        return None
    title = ""
    title_node = soup.select_one("h1, h2, title")
    if title_node is not None:
        title = _clean_text(title_node.get_text(" ", strip=True).replace(" - ", " "))
    title = title or _short_title(text)
    if not title:
        return None
    city, district = _extract_location(text)
    rent = _extract_rent(text)
    management_fee = _extract_management_fee({}, text)
    total_monthly_cost = rent + management_fee if rent is not None and management_fee is not None else rent
    listing_url = canonicalize_listing_url(page_url)
    listing_id = _extract_listing_id_from_url(listing_url, spec) or canonical_listing_id(spec.name, listing_url)
    raw_json = _raw_json(
        parser_name="detail_text",
        page_url=page_url,
        canonical_url=listing_url,
        source_listing_id=listing_id,
        raw_text=text,
        raw_payload=None,
        management_fee=management_fee,
    )
    return Listing(
        source=spec.name,
        listing_id=listing_id,
        url=listing_url,
        title=title[:120],
        city=city,
        district=district,
        address=_extract_address_from_text(text, city, district),
        rent=rent,
        total_monthly_cost=total_monthly_cost,
        area_ping=_extract_area(text),
        room_type=_extract_room_type(text),
        floor=_extract_floor(text),
        description=text[:4000],
        tags=_extract_tags({}, text),
        has_rent_subsidy=_has_positive(text, RENT_SUBSIDY_KEYWORDS, RENT_SUBSIDY_NEGATIVE),
        has_tax_registration=_has_positive(text, TAX_KEYWORDS, TAX_NEGATIVE),
        has_independent_washer=_has_positive(text, WASHER_KEYWORDS, SHARED_WASHER_NEGATIVE),
        has_garbage_collection=_has_positive(text, GARBAGE_COLLECTION_KEYWORDS, GARBAGE_COLLECTION_NEGATIVE),
        image_urls=_extract_node_images(soup, page_url)[:3],
        raw_json=raw_json,
        status="active",
    )


def _raw_json(
    *,
    parser_name: str,
    page_url: str,
    canonical_url: str,
    source_listing_id: str | None,
    raw_text: str,
    raw_payload: object | None,
    management_fee: int | None,
) -> dict:
    gender_restriction = _extract_gender_restriction(raw_text, raw_payload)
    raw_json = {
        "parser": parser_name,
        "page_url": page_url,
        "canonical_url": canonical_url,
        "source_listing_id": source_listing_id,
        "rent_monthly": _extract_rent(raw_text),
        "management_fee": management_fee,
        "layout": _extract_layout(raw_text),
        "total_floor": _extract_total_floor(raw_text),
        "updated_at_text": _extract_updated_at_text(raw_text),
        "listed_at_text": _extract_listed_at_text(raw_text),
        "update_age_days": _extract_update_age_days(raw_text),
        "raw_text": raw_text[:4000],
    }
    if gender_restriction:
        raw_json["gender_restriction"] = gender_restriction
    if raw_payload is not None:
        raw_json["raw_payload"] = raw_payload
    return raw_json


def _first_string(data: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _flatten_text(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = _clean_text(value)
        return [text] if text else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        result: list[str] = []
        for key, child in value.items():
            if key in {"style", "script"}:
                continue
            result.extend(_flatten_text(child))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_text(item))
        return result
    return []


def _extract_mapping_listing_id(data: dict) -> str | None:
    for key in ID_KEYS:
        value = data.get(key)
        if value is None:
            continue
        match = re.search(r"[A-Za-z0-9][A-Za-z0-9_-]{3,}", str(value))
        if match:
            return match.group(0)
    return None


def _extract_price_from_mapping(data: dict) -> int | None:
    for key in PRICE_KEYS:
        parsed = _safe_int(data.get(key))
        if parsed is not None:
            return parsed
    offers = data.get("offers")
    if isinstance(offers, dict):
        for key in PRICE_KEYS:
            parsed = _safe_int(offers.get(key))
            if parsed is not None:
                return parsed
    if isinstance(offers, list):
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            for key in PRICE_KEYS:
                parsed = _safe_int(offer.get(key))
                if parsed is not None:
                    return parsed
    return None


def _extract_management_fee(data: dict, text: str) -> int | None:
    for key in FEE_KEYS:
        parsed = _safe_int(data.get(key))
        if parsed is not None and parsed <= 20_000:
            return parsed
    patterns = [
        r"(?:管理費|管費)\D{0,8}(\d[\d,]{1,5})",
        r"(\d[\d,]{1,5})\s*(?:元)?\s*(?:管理費|管費)",
    ]
    values: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = int(match.group(1).replace(",", ""))
            if 0 <= value <= 20_000:
                values.append(value)
    return max(values) if values else None


def _extract_area_from_mapping(data: dict) -> float | None:
    for key in AREA_KEYS:
        parsed = _safe_float(data.get(key))
        if parsed is not None:
            return parsed
    return None


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d[\d,]*", str(value))
    if not match:
        return None
    parsed = int(match.group(0).replace(",", ""))
    return parsed if 1_000 <= parsed <= 300_000 else None


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    parsed = float(match.group(0))
    return parsed if 1 <= parsed <= 300 else None


def _extract_rent(text: str) -> int | None:
    patterns = [
        r"(?:租金|月租|價格|房租|租)\D{0,12}(\d[\d,]{3,6})",
        r"(\d[\d,]{3,6})\s*(?:元/月|元|/月|每月)",
    ]
    values: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = int(match.group(1).replace(",", ""))
            if 3_000 <= value <= 300_000:
                values.append(value)
    return max(values) if values else None


def _extract_area(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*坪", text)
    if not match:
        return None
    parsed = float(match.group(1))
    return parsed if 1 <= parsed <= 300 else None


def _extract_room_type(text: str) -> str | None:
    if "獨立套房" in text or "獨套" in text:
        return "獨立套房"
    if "分租套房" in text or "分套" in text:
        return "分租套房"
    if "套房" in text:
        return "套房"
    if "雅房" in text:
        return "雅房"
    if "整層住家" in text or "整層" in text:
        return "整層住家"
    if "車位" in text:
        return "車位"
    if "店面" in text:
        return "店面"
    if "辦公" in text:
        return "辦公"
    return None


def _extract_layout(text: str) -> str | None:
    match = re.search(r"(\d+\s*房(?:\s*\d+\s*廳)?(?:\s*\d+\s*衛)?(?:\s*\d+\s*陽台)?)", text)
    if match:
        return re.sub(r"\s+", "", match.group(1))
    match = re.search(r"(\d+\s*廳\s*\d+\s*衛)", text)
    return re.sub(r"\s+", "", match.group(1)) if match else None


def _extract_floor(text: str) -> str | None:
    match = re.search(
        r"(?<!\w)((?:B\d+|地下\d+|頂樓加蓋|頂樓|\d+|--)\s*/\s*(?:B?\d+|地下\d+|\d+|--)\s*(?:F|樓))",
        text,
        re.I,
    )
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(1)).upper()


def _extract_total_floor(text: str) -> str | None:
    floor = _extract_floor(text)
    if not floor or "/" not in floor:
        return None
    return floor.split("/", 1)[1].replace("樓", "").replace("F", "")


def _extract_location(
    text: str,
    *,
    preferred_city: str | None = None,
    preferred_district: str | None = None,
) -> tuple[str | None, str | None]:
    """Extract a Taiwan city/district pair without guessing ambiguous districts.

    Several district names are reused by different cities (for example 大安區,
    中正區 and 東區).  A structured city value, or a full city/county name in
    the text, scopes those names.  A district by itself is accepted only when it
    belongs to exactly one city in the Taiwan location table.
    """

    normalized_text = normalize_location_name(text)
    city = _canonical_city_name(preferred_city) or _city_from_text(normalized_text)

    structured_district = _canonical_district_name(preferred_district)
    if structured_district:
        if city and structured_district in TAIWAN_591_LOCATIONS[city][1]:
            return city, structured_district
        if not city:
            district_city = city_for_district(structured_district)
            if district_city:
                return district_city, structured_district

    if city:
        district = _first_district_in_text(
            normalized_text,
            tuple(TAIWAN_591_LOCATIONS[city][1]),
        )
        return city, district

    district = _first_district_in_text(normalized_text, DISTRICT_NAMES_BY_LENGTH, unique_only=True)
    if not district:
        return None, None
    return city_for_district(district), district


def _canonical_city_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_location_name(value)
    if normalized in TAIWAN_591_LOCATIONS:
        return normalized
    for city in CITY_NAMES_BY_LENGTH:
        if city in normalized:
            return city

    # Structured APIs sometimes omit 市/縣.  Only resolve aliases that identify
    # one official region; 新竹 and 嘉義 intentionally remain ambiguous.
    matches = [city for city in TAIWAN_591_LOCATIONS if city[:-1] == normalized]
    return matches[0] if len(matches) == 1 else None


def _canonical_district_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_location_name(value)
    if normalized in DISTRICT_CITIES:
        return normalized
    return _first_district_in_text(normalized, DISTRICT_NAMES_BY_LENGTH)


def _city_from_text(text: str) -> str | None:
    matches = ((text.find(city), city) for city in CITY_NAMES_BY_LENGTH if city in text)
    return min(matches, default=(0, None))[1]


def _first_district_in_text(
    text: str,
    districts: Iterable[str],
    *,
    unique_only: bool = False,
) -> str | None:
    matches: list[tuple[int, int, str]] = []
    for district in districts:
        if unique_only and len(DISTRICT_CITIES.get(district, ())) != 1:
            continue
        position = text.find(district)
        if position >= 0:
            matches.append((position, -len(district), district))
    return min(matches, default=(0, 0, None))[2]


def _extract_address_from_text(
    text: str,
    city: str | None,
    district: str | None,
) -> str | None:
    if not district:
        return None

    normalized_text = normalize_location_name(text)
    city_prefix = ""
    if city:
        city_prefix = rf"(?:{re.escape(city)})?\s*"
    match = re.search(
        rf"({city_prefix}{re.escape(district)}[^\n，,。|]{{0,60}})",
        normalized_text,
    )
    if match:
        return match.group(1).strip()
    for line in normalized_text.splitlines():
        if district in line and len(line) <= 120:
            return line.strip()
    return None


RENT_SUBSIDY_KEYWORDS = ("租金補貼", "租補", "可申請補助", "補助")
RENT_SUBSIDY_NEGATIVE = ("不可租補", "不能租補", "不租補", "無租補")
TAX_KEYWORDS = ("可報稅", "報稅", "可入籍", "設籍", "社會住宅", "社宅")
TAX_NEGATIVE = ("不可報稅", "不能報稅", "不報稅", "不可入籍", "不能入籍")
WASHER_KEYWORDS = ("獨立洗衣機", "室內洗衣機", "專用洗衣機", "私人洗衣機", "獨洗", "洗衣機")
SHARED_WASHER_NEGATIVE = ("共用洗衣機", "公共洗衣機", "投幣式洗衣")
GARBAGE_COLLECTION_KEYWORDS = ("垃圾代收", "代收垃圾", "垃圾代丟", "代丟垃圾", "專人收垃圾")
GARBAGE_COLLECTION_NEGATIVE = ("無垃圾代收", "不代收垃圾", "自行倒垃圾", "垃圾自行處理")


def _has_positive(text: str, positive: tuple[str, ...], negative: tuple[str, ...]) -> bool | None:
    if any(word in text for word in negative):
        return False
    if any(word in text for word in positive):
        return True
    return None


def _extract_tags(data: dict, text: str) -> list[str]:
    tags: list[str] = []
    for key in TAG_KEYS:
        for item in _flatten_text(data.get(key)):
            if item and item not in tags:
                tags.append(item)
    for label, value in (
        ("租補", _has_positive(text, RENT_SUBSIDY_KEYWORDS, RENT_SUBSIDY_NEGATIVE)),
        ("可報稅", _has_positive(text, TAX_KEYWORDS, TAX_NEGATIVE)),
        ("獨立洗衣機", _has_positive(text, WASHER_KEYWORDS, SHARED_WASHER_NEGATIVE)),
        ("垃圾代收", _has_positive(text, GARBAGE_COLLECTION_KEYWORDS, GARBAGE_COLLECTION_NEGATIVE)),
    ):
        if value is True and label not in tags:
            tags.append(label)
    return tags[:12]


FEMALE_ONLY_PATTERNS = (
    r"(?<!不)(?:限|只限|僅限|限租|限收|只租|僅租|限住|限入住)\s*(?:單身)?\s*(?:女生|女性|女學生|女上班族|女房客|女租客|女)\s*(?:入住|租住|承租|租客|房客)?",
    r"(?:女生|女性|女學生|女上班族|女房客|女租客|女)\s*(?:限定|限住|限租|入住|租住|承租|專屬|優先|佳)",
)


def _extract_gender_restriction(text: str, raw_payload: object | None) -> str | None:
    normalized = re.sub(r"\s+", "", text)
    for pattern in FEMALE_ONLY_PATTERNS:
        match = re.search(pattern, normalized)
        if match:
            return match.group(0)[:120]
    if isinstance(raw_payload, dict):
        values: list[str] = []
        for key in GENDER_KEYS:
            values.extend(_flatten_text(raw_payload.get(key)))
        for value in values:
            if _extract_gender_restriction(value, None):
                return value[:120]
    return None


def _extract_updated_at_text(text: str) -> str | None:
    patterns = (
        r"(?:新上架|剛剛更新|今天更新|今日更新|昨日更新|昨天更新)",
        r"\d+\s*(?:分鐘|小時|天)(?:內|前)?更新",
        r"更新(?:時間|日期)?[:：]?\s*[0-9/\-.年月日 ]{4,20}",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None


def _extract_listed_at_text(text: str) -> str | None:
    match = re.search(r"(?:刊登|上架)(?:時間|日期)?[:：]?\s*[0-9/\-.年月日 ]{4,20}", text)
    return match.group(0) if match else None


def _extract_update_age_days(text: str) -> int | None:
    if any(marker in text for marker in ("新上架", "剛剛更新", "今天更新", "今日更新")):
        return 0
    if re.search(r"\d+\s*(?:分鐘|小時)(?:內|前)?更新", text):
        return 0
    match = re.search(r"(\d+)\s*天(?:內|前)?更新", text)
    if match:
        return int(match.group(1))
    if "昨日更新" in text or "昨天更新" in text:
        return 1
    return None


def _extract_json_images(page_url: str, data: dict) -> list[str]:
    urls: list[str] = []
    for key in IMAGE_KEYS:
        for url in _flatten_image_urls(page_url, data.get(key)):
            if url and url not in urls:
                urls.append(url)
    return urls


def _flatten_image_urls(page_url: str, value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        url = _normalize_image_url(page_url, value)
        return [url] if url else []
    if isinstance(value, dict):
        urls: list[str] = []
        for key in ("url", "src", "path", "image", "filename"):
            urls.extend(_flatten_image_urls(page_url, value.get(key)))
        return urls
    if isinstance(value, list):
        urls: list[str] = []
        for item in value:
            urls.extend(_flatten_image_urls(page_url, item))
        return urls
    return []


def _extract_card_images(anchor: Tag, page_url: str, listing_url: str) -> list[str]:
    for node in _card_image_candidates(anchor, page_url, listing_url):
        urls = _extract_node_images(node, page_url)
        if urls:
            return urls
    return []


def _card_image_candidates(anchor: Tag, page_url: str, listing_url: str) -> list[Tag]:
    candidates = [anchor]
    parent = anchor.parent
    for _ in range(4):
        if not isinstance(parent, Tag):
            break
        if _candidate_matches_listing(parent, page_url, listing_url):
            candidates.append(parent)
        parent = parent.parent
    return candidates


def _candidate_matches_listing(node: Tag, page_url: str, listing_url: str) -> bool:
    other_urls: set[str] = set()
    for link in node.select("a[href]"):
        candidate = canonicalize_listing_url(_normalize_url(page_url, str(link.get("href", ""))))
        if candidate:
            other_urls.add(candidate)
    return not other_urls or other_urls == {listing_url}


def _extract_node_images(node: Tag | BeautifulSoup, page_url: str) -> list[str]:
    urls: list[str] = []
    for image in node.select("img, source"):
        for value in _image_attribute_values(image):
            _append_image_url(urls, page_url, value)
    for element in node.select("[style]"):
        for value in _style_image_values(str(element.get("style") or "")):
            _append_image_url(urls, page_url, value)
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


def _append_image_url(urls: list[str], page_url: str, value: str) -> None:
    url = _normalize_image_url(page_url, value)
    if url and url not in urls:
        urls.append(url)


def _normalize_image_url(page_url: str, value: str) -> str | None:
    value = html_lib.unescape(value).strip()
    if not value:
        return None
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/"):
        return urljoin(page_url, value)
    return None


def _title_from_card(anchor: Tag, text: str) -> str:
    title_node = anchor.select_one("h1, h2, h3, [class*=title], [class*=Title]")
    if title_node is not None:
        title = _clean_text(title_node.get_text(" ", strip=True))
        if title:
            return title
    anchor_text = _clean_text(anchor.get_text(" ", strip=True))
    if anchor_text and len(anchor_text) <= 160:
        return anchor_text
    return _short_title(text)


def _card_text(anchor: Tag) -> str:
    candidates = [anchor]
    parent = anchor.parent
    for _ in range(4):
        if not isinstance(parent, Tag):
            break
        candidates.append(parent)
        parent = parent.parent
    texts = [_clean_text(node.get_text(" ", strip=True)) for node in candidates]
    texts = [text for text in texts if text]
    if not texts:
        return ""
    return min(texts, key=lambda value: abs(len(value) - 280))[:4000]


def _short_title(text: str) -> str:
    for line in re.split(r"[\n。]", text):
        line = _clean_text(line)
        if len(line) >= 4 and not re.fullmatch(r"[\d,./坪元月樓F\s|]+", line):
            return line[:120]
    return _clean_text(text)[:120]


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(str(value))).strip()


def _normalize_url(page_url: str, value: str) -> str:
    value = html_lib.unescape(value).strip().strip("'\"")
    if value.startswith("//"):
        return "https:" + value
    return urljoin(page_url, value)


def canonicalize_listing_url(url: str) -> str:
    parsed = urlparse(url)
    kept_query: dict[str, list[str]] = {}
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, values in query.items():
        lowered = key.lower()
        if lowered in TRACKING_QUERY_KEYS or lowered.startswith("utm_"):
            continue
        kept_query[key] = values
    encoded = urlencode(kept_query, doseq=True)
    path = parsed.path.rstrip("/") or parsed.path
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, encoded, ""))


def _looks_like_detail_url(url: str, spec: PublicRentalSourceSpec) -> bool:
    if not url:
        return False
    return any(re.search(pattern, url) for pattern in spec.detail_url_patterns)


def _extract_listing_id_from_url(url: str, spec: PublicRentalSourceSpec) -> str | None:
    for pattern in spec.detail_url_patterns:
        match = re.search(pattern, url)
        if match and match.groups():
            values = [item for item in match.groups() if item]
            if values:
                return "_".join(values)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("id", "case", "caseId", "houseId", "houseid"):
        if query.get(key):
            return query[key][0]
    parts = [part for part in parsed.path.split("/") if part]
    if parts:
        candidate = parts[-1]
        if len(candidate) >= 4:
            return re.sub(r"\W+", "_", candidate)
    return None


def _needs_detail_enrichment(listing: Listing) -> bool:
    return (
        listing.rent is None
        or listing.area_ping is None
        or not listing.district
        or not listing.room_type
        or len(listing.description) < 40
    )


def _merge_detail_listing(base: Listing, detail: Listing) -> Listing:
    for attr in (
        "title",
        "city",
        "district",
        "address",
        "rent",
        "total_monthly_cost",
        "area_ping",
        "room_type",
        "floor",
        "has_rent_subsidy",
        "has_tax_registration",
        "has_independent_washer",
        "has_garbage_collection",
    ):
        if getattr(base, attr) in (None, "") and getattr(detail, attr) not in (None, ""):
            setattr(base, attr, getattr(detail, attr))
    if len(detail.description) > len(base.description):
        base.description = detail.description
    if not base.image_urls and detail.image_urls:
        base.image_urls = detail.image_urls[:3]
    for tag in detail.tags:
        if tag not in base.tags:
            base.tags.append(tag)
    base.raw_json["detail_raw_json"] = detail.raw_json
    return base


def _filter_recent_listings(listings: list[Listing], max_age_days: int) -> list[Listing]:
    recent: list[Listing] = []
    for listing in listings:
        age = listing.raw_json.get("update_age_days")
        if isinstance(age, int) and age > max_age_days:
            continue
        recent.append(listing)
    return recent


def _dedupe(listings: list[Listing]) -> list[Listing]:
    seen: set[tuple[str, str]] = set()
    result: list[Listing] = []
    for listing in listings:
        key = (listing.source, listing.listing_id)
        if key not in seen:
            seen.add(key)
            result.append(listing)
    return result


def fingerprint_listing(listing: Listing) -> str | None:
    city = listing.city or ""
    district = listing.district or ""
    cost = listing.total_monthly_cost or listing.rent
    if not city or not district or not cost or listing.area_ping is None:
        return None
    address = _normalize_fingerprint_text(listing.address or "")
    title = _normalize_fingerprint_text(listing.title)
    room_type = listing.room_type or ""
    area = round(float(listing.area_ping), 1)
    payload = "|".join([city, district, address[:18], str(cost), str(area), room_type, title[:24]])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _normalize_fingerprint_text(value: str) -> str:
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[^\w\u4e00-\u9fff]", "", value)
    for token in ("近捷運", "全新", "採光", "稀有", "專約", "急租"):
        value = value.replace(token, "")
    return value
