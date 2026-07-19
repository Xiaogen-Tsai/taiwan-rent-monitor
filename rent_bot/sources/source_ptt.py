from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from rent_bot.filters import extract_first_int
from rent_bot.models import Listing, canonical_listing_id
from rent_bot.sources.base import BaseSource, SourceResult
from rent_bot.taiwan_591_locations import TAIWAN_591_LOCATIONS, normalize_location_name

logger = logging.getLogger(__name__)


BOARD_URL = "https://www.ptt.cc/bbs/Rent_tao/index.html"
BASE_URL = "https://www.ptt.cc"
PTT_CITY = "桃園市"
DISTRICTS = tuple(TAIWAN_591_LOCATIONS[PTT_CITY][1])
DISTRICT_STEMS = {district.removesuffix("區"): district for district in DISTRICTS}

# Rent_tao is the Taoyuan rental board.  These aliases are useful within its
# bracketed area field, but are intentionally not treated as Taiwan-wide names.
# Ambiguous place names such as 後站, 青埔 and 龍岡 are omitted.
BRACKET_AREA_TO_DISTRICT = {
    **DISTRICT_STEMS,
    "桃區": "桃園區",
    "南崁": "蘆竹區",
    "內壢": "中壢區",
    "埔心": "楊梅區",
}


class SourcePTT(BaseSource):
    name = "ptt_rent_tao"

    def fetch(self) -> SourceResult:
        if not self.settings.source_ptt_enabled:
            return SourceResult(listings=[], errors=["PTT source disabled"])
        selected_cities = {
            normalize_location_name(city) for city in self.settings.allowed_city_districts
        }
        if PTT_CITY not in selected_cities:
            logger.info("Skipping PTT Rent_tao because the selected locations do not include %s", PTT_CITY)
            return SourceResult(listings=[], errors=[])

        errors: list[str] = []
        listings: list[Listing] = []
        page_url: str | None = BOARD_URL
        seen_article_urls: set[str] = set()

        for _page in range(self.settings.ptt_max_pages):
            if not page_url:
                break
            try:
                response = self.polite_get(page_url)
                if response is None:
                    errors.append(f"Skipped by robots or access control: {page_url}")
                    break
                soup = BeautifulSoup(response.text, "html.parser")
                article_urls = self._article_urls(soup)
                for article_url in article_urls:
                    if article_url in seen_article_urls:
                        continue
                    seen_article_urls.add(article_url)
                    try:
                        listing = self._fetch_article(article_url)
                        if listing is not None:
                            listings.append(listing)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Failed to parse PTT article %s: %s", article_url, exc)
                        errors.append(f"{article_url}: {exc}")
                page_url = self._previous_page_url(soup)
            except Exception as exc:  # noqa: BLE001
                logger.exception("PTT source failed on %s", page_url)
                errors.append(f"{page_url}: {exc}")
                break

        return SourceResult(listings=listings, errors=errors)

    def _article_urls(self, soup: BeautifulSoup) -> list[str]:
        urls: list[str] = []
        for entry in soup.select("div.r-ent"):
            title = entry.select_one(".title")
            link = title.select_one("a") if title else None
            if not link or not link.get("href"):
                continue
            title_text = link.get_text(" ", strip=True)
            if "公告" in title_text:
                continue
            if not _maybe_relevant_title(title_text):
                continue
            urls.append(urljoin(BASE_URL, link["href"]))
        return urls

    def _previous_page_url(self, soup: BeautifulSoup) -> str | None:
        for link in soup.select("a.btn.wide"):
            if "上頁" in link.get_text(strip=True) and link.get("href"):
                return urljoin(BASE_URL, link["href"])
        return None

    def _fetch_article(self, url: str) -> Listing | None:
        response = self.polite_get(url, referer=BOARD_URL)
        if response is None:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        main = soup.select_one("#main-content")
        if main is None:
            return None

        title = _meta_value(soup, "標題") or _title_from_h1(soup) or url
        content = _clean_article_text(main)
        if not _maybe_relevant_text(title + "\n" + content):
            return None

        city, district = _extract_location(title + "\n" + content)
        rent = _extract_rent(title + "\n" + content)
        management_fee = _extract_management_fee(content)
        total_monthly_cost = rent + management_fee if rent is not None and management_fee is not None else rent
        area = _extract_area(title + "\n" + content)
        room_type = _extract_room_type(title + "\n" + content)
        address = _extract_address(content, district)
        floor = _extract_field(content, ("樓層", "所在樓層", "樓別"))
        image_urls = _extract_images(soup)
        tags = []
        if "租補" in content or "租金補貼" in content:
            tags.append("租補")
        if "可報稅" in content or "報稅" in content:
            tags.append("報稅")
        if "獨立洗衣機" in content or "室內洗衣機" in content or "獨洗" in content:
            tags.append("獨立洗衣機")
        if "垃圾代收" in content or "代收垃圾" in content:
            tags.append("垃圾代收")

        listing_id = _ptt_article_id(url) or canonical_listing_id(self.name, url)
        return Listing(
            source=self.name,
            listing_id=listing_id,
            url=url,
            title=title,
            city=city,
            district=district,
            address=address,
            rent=rent,
            total_monthly_cost=total_monthly_cost,
            area_ping=area,
            room_type=room_type,
            floor=floor,
            description=content[:4000],
            tags=tags,
            image_urls=image_urls[:3],
            status="active",
            raw_json={"source_url": url, "management_fee": management_fee},
        )


def _meta_value(soup: BeautifulSoup, label: str) -> str | None:
    tags = soup.select("span.article-meta-tag")
    values = soup.select("span.article-meta-value")
    for tag, value in zip(tags, values, strict=False):
        if tag.get_text(strip=True) == label:
            return value.get_text(" ", strip=True)
    return None


def _title_from_h1(soup: BeautifulSoup) -> str | None:
    title = soup.select_one("title")
    if title:
        return title.get_text(" ", strip=True).replace(" - 批踢踢實業坊", "")
    return None


def _clean_article_text(main: BeautifulSoup) -> str:
    for selector in [
        "div.article-metaline",
        "div.article-metaline-right",
        "span.f2",
        ".push",
    ]:
        for node in main.select(selector):
            node.decompose()
    text = main.get_text("\n", strip=True)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("--"):
            break
        if line.startswith("※ 發信站:"):
            break
        lines.append(line)
    return "\n".join(lines)


def _maybe_relevant_title(text: str) -> bool:
    normalized = normalize_location_name(text)
    if _bracket_area(normalized) is not None:
        return True
    return "桃園市" in normalized and any(
        token in normalized for token in ("出租", "租屋", "套房", "雅房", "整層")
    )


def _maybe_relevant_text(text: str) -> bool:
    if any(word in text for word in ("徵室友", "求租", "交換", "短租")) and "出租" not in text:
        return False
    return _extract_location(text)[1] is not None


def _extract_location(text: str) -> tuple[str | None, str | None]:
    normalized = normalize_location_name(text)
    address_field = _extract_field(
        normalized,
        ("地址", "地點", "位置", "房屋地點", "租屋地點", "房屋地址", "租屋地址"),
    )
    if address_field:
        explicit_city = _first_official_city(address_field)
        if explicit_city and explicit_city != PTT_CITY:
            return None, None
        district = _taoyuan_district_from_text(address_field, allow_area_aliases=True)
        if district:
            return PTT_CITY, district

    district = _taoyuan_district_from_text(normalized)
    if district:
        return PTT_CITY, district

    area = _bracket_area(normalized)
    if area:
        district = _taoyuan_district_from_text(area, allow_area_aliases=True)
        if district:
            return PTT_CITY, district

    return (PTT_CITY, None) if "桃園市" in normalized else (None, None)


def _bracket_area(text: str) -> str | None:
    match = re.search(
        r"[\[［][^/\]］]{1,16}/\s*桃園(?:市|縣)?\s*/\s*([^/\]］\r\n]+)[\]］]",
        normalize_location_name(text),
    )
    return match.group(1).strip() if match else None


def _first_official_city(text: str) -> str | None:
    normalized = normalize_location_name(text)
    matches = [
        (normalized.find(city), city)
        for city in TAIWAN_591_LOCATIONS
        if city in normalized
    ]
    return min(matches, default=(0, None))[1]


def _taoyuan_district_from_text(text: str, *, allow_area_aliases: bool = False) -> str | None:
    normalized = normalize_location_name(text)
    matches = [
        (normalized.find(district), -len(district), district)
        for district in DISTRICTS
        if district in normalized
    ]
    if matches:
        return min(matches)[2]

    # Keep pre-2014 addresses usable, such as 桃園縣龜山鄉 and 桃園縣桃園市.
    for stem, district in DISTRICT_STEMS.items():
        if re.search(rf"桃園(?:市|縣)\s*{re.escape(stem)}(?:區|市|鎮|鄉)", normalized):
            return district

    if allow_area_aliases:
        compact = re.sub(r"[\s・·｜|／/\-—_].*$", "", normalized).strip()
        for alias in sorted(BRACKET_AREA_TO_DISTRICT, key=len, reverse=True):
            if compact == alias or compact.startswith(alias):
                return BRACKET_AREA_TO_DISTRICT[alias]
    return None


def _extract_rent(text: str) -> int | None:
    patterns = [
        r"(?:租金|月租|價格|房租|租)\D{0,8}(\d[\d,]{3,6})",
        r"(\d[\d,]{3,6})\s*(?:元/月|元|\/月|每月)",
    ]
    values: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = int(match.group(1).replace(",", ""))
            if 3_000 <= value <= 80_000:
                values.append(value)
    return min(values) if values else None


def _extract_management_fee(text: str) -> int | None:
    patterns = [
        r"(?:管理費|管費)\D{0,8}(\d[\d,]{1,5})",
        r"(\d[\d,]{1,5})\s*(?:元)?\s*(?:管理費|管費)",
    ]
    values: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = int(match.group(1).replace(",", ""))
            if 0 <= value <= 10_000:
                values.append(value)
    return max(values) if values else None


def _extract_area(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:坪|p|P)", text)
    if not match:
        return None
    value = float(match.group(1))
    if 1 <= value <= 80:
        return value
    return None


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
    if "雅房" in text:
        return "雅房"
    return None


def _extract_address(text: str, district: str | None) -> str | None:
    explicit = _extract_field(
        text,
        ("地址", "地點", "位置", "房屋地點", "租屋地點", "房屋地址", "租屋地址"),
    )
    if explicit:
        return explicit[:120]
    if district:
        for line in text.splitlines():
            if district in line and len(line) <= 120:
                return line
    return None


def _extract_field(text: str, labels: tuple[str, ...]) -> str | None:
    for line in text.splitlines():
        normalized = line.strip()
        for label in labels:
            pattern = rf"^{re.escape(label)}\s*[:：]\s*(.+)$"
            match = re.search(pattern, normalized)
            if match:
                return match.group(1).strip()
    return None


def _extract_images(soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for link in soup.select("a[href]"):
        href = link["href"].strip()
        if re.search(r"\.(jpg|jpeg|png|webp)(?:\?|$)", href, re.I) or "imgur.com" in href:
            if href.startswith("//"):
                href = "https:" + href
            if href.startswith("http") and href not in urls:
                urls.append(href)
    return urls


def _ptt_article_id(url: str) -> str | None:
    match = re.search(r"/([^/]+)\.html$", url)
    return match.group(1) if match else None


def _extract_first_price_like(text: str) -> int | None:
    value = extract_first_int(text)
    if value and 3_000 <= value <= 80_000:
        return value
    return None
