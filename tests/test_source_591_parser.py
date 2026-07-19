from __future__ import annotations

import json
import unittest
from pathlib import Path

from rent_bot.filters import listing_has_female_only_restriction
from rent_bot.config import Settings
from rent_bot.sources.base import SourceErrorCode
from rent_bot.sources.source_591 import (
    Source591,
    _detect_access_wall,
    _expand_section_search_urls,
    _extract_address_from_text,
    _extract_update_age_days,
    _paginated_search_urls,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "591_search_sample.html"


class Source591ParserTest(unittest.TestCase):
    def test_parse_public_jsonld_fixture(self) -> None:
        source = Source591(Settings())
        html = FIXTURE.read_text(encoding="utf-8")

        listings = source._parse_public_html("https://rent.591.com.tw/list?kind=2", html)

        self.assertEqual(len(listings), 2)
        first = listings[0]
        self.assertEqual(first.source, "591")
        self.assertEqual(first.listing_id, "123456")
        self.assertEqual(first.city, "台北市")
        self.assertEqual(first.district, "大安區")
        self.assertEqual(first.rent, 15800)
        self.assertEqual(first.total_monthly_cost, 15800)
        self.assertEqual(first.area_ping, 7.0)
        self.assertEqual(first.room_type, "獨立套房")
        self.assertEqual(len(first.image_urls), 2)

    def test_parse_embedded_browser_state(self) -> None:
        source = Source591(Settings())
        state = {
            "props": {
                "pageProps": {
                    "items": [
                        {
                            "id": "777888",
                            "title": "永福橋頂溪小資族",
                            "price": "12000",
                            "area": "8坪",
                            "region_name": "新北市",
                            "section_name": "永和區",
                            "address": "永和區-竹林路135巷",
                            "kind_name": "獨立套房",
                            "desc": "近捷運，可租金補貼，室內獨立洗衣機",
                            "photo_list": [{"url": "//hp1.591.com.tw/house/sample.jpg"}],
                            "tags": ["近捷運", "租金補貼"],
                            "gender": "此房屋限女生租住",
                        }
                    ]
                }
            }
        }
        html = (
            '<script id="__NEXT_DATA__" type="application/json">'
            f"{json.dumps(state, ensure_ascii=False)}"
            "</script>"
        )

        listings = source._parse_public_html("https://rent.591.com.tw/list?kind=2&region=3&section=37", html)

        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.listing_id, "777888")
        self.assertEqual(listing.url, "https://rent.591.com.tw/rent-detail-777888.html")
        self.assertEqual(listing.city, "新北市")
        self.assertEqual(listing.district, "永和區")
        self.assertEqual(listing.rent, 12000)
        self.assertEqual(listing.area_ping, 8.0)
        self.assertEqual(listing.room_type, "獨立套房")
        self.assertEqual(listing.image_urls, ["https://hp1.591.com.tw/house/sample.jpg"])
        self.assertEqual(listing.raw_json["gender_restriction"], "此房屋限女生租住")
        self.assertTrue(listing_has_female_only_restriction(listing))

    def test_parse_nested_gender_restriction_is_filterable(self) -> None:
        source = Source591(Settings())
        state = {
            "props": {
                "pageProps": {
                    "items": [
                        {
                            "id": "777889",
                            "title": "永福橋頂溪小資族",
                            "price": "12000",
                            "area": "8坪",
                            "region_name": "新北市",
                            "section_name": "永和區",
                            "address": "永和區-竹林路135巷",
                            "kind_name": "獨立套房",
                            "desc": "近捷運，可租金補貼，室內獨立洗衣機",
                            "rules": {"入住": {"text": "此房屋限女生租住"}},
                        }
                    ]
                }
            }
        }
        html = (
            '<script id="__NEXT_DATA__" type="application/json">'
            f"{json.dumps(state, ensure_ascii=False)}"
            "</script>"
        )

        listings = source._parse_public_html("https://rent.591.com.tw/list?kind=2&region=3&section=37", html)

        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.raw_json["gender_restriction"], "此房屋限女生租住")
        self.assertTrue(listing_has_female_only_restriction(listing))

    def test_parse_anchor_card_fallback(self) -> None:
        source = Source591(Settings())
        html = """
        <article class="item">
          <a href="https://rent.591.com.tw/888999">
            <img src="//hp2.591.com.tw/house/card.jpg">
            <div>永和頂溪一樓精緻套房 採光佳 分租套房 8坪 2F/4F 永和區-文化路90巷22弄2號 14,000 元/月</div>
          </a>
        </article>
        """

        listings = source._parse_public_html("https://rent.591.com.tw/list?kind=3&region=3&section=37", html)

        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.listing_id, "888999")
        self.assertEqual(listing.city, "新北市")
        self.assertEqual(listing.district, "永和區")
        self.assertEqual(listing.rent, 14000)
        self.assertEqual(listing.area_ping, 8.0)
        self.assertEqual(listing.room_type, "分租套房")
        self.assertEqual(listing.floor, "2F/4F")
        self.assertEqual(listing.image_urls, ["https://hp2.591.com.tw/house/card.jpg"])

    def test_new_taipei_xindian_section_maps_to_district(self) -> None:
        source = Source591(Settings())
        html = """
        <article class="item">
          <a href="https://rent.591.com.tw/555666">
            捷運新店總站獨立套房 獨立套房 8坪 12,000 元/月
          </a>
        </article>
        """

        listings = source._parse_public_html("https://rent.591.com.tw/list?kind=2&region=3&section=34", html)

        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].city, "新北市")
        self.assertEqual(listings[0].district, "新店區")

    def test_nationwide_section_url_supplies_location_for_sparse_card(self) -> None:
        source = Source591(Settings())
        html = """
        <article class="item">
          <a href="https://rent.591.com.tw/555667">
            港邊採光獨立套房 8坪 12,000 元/月
          </a>
        </article>
        """

        listings = source._parse_public_html(
            "https://rent.591.com.tw/list?kind=2&region=17&section=247",
            html,
        )

        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].city, "高雄市")
        self.assertEqual(listings[0].district, "鼓山區")

    def test_duplicate_district_name_uses_page_city_context(self) -> None:
        source = Source591(Settings())
        html = """
        <article class="item">
          <a href="https://rent.591.com.tw/555668">
            東區採光分租套房 8坪 9,000 元/月
          </a>
        </article>
        """

        listings = source._parse_public_html(
            "https://rent.591.com.tw/list?kind=3&region=8&section=99",
            html,
        )

        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].city, "台中市")
        self.assertEqual(listings[0].district, "東區")

    def test_nationwide_address_extraction_uses_resolved_city_and_district(self) -> None:
        address = _extract_address_from_text(
            "採光套房 臺中市東區復興路四段123號 近車站",
            city="臺中市",
            district="東區",
        )

        self.assertEqual(address, "台中市東區復興路四段123號")

    def test_anchor_card_images_can_come_from_same_card_siblings(self) -> None:
        source = Source591(Settings())
        html = """
        <section>
          <article class="item">
            <div class="photo" style="background-image: url('//hp1.591.com.tw/house/bg.jpg')"></div>
            <a href="https://rent.591.com.tw/111222">
              永和頂溪採光套房 分租套房 8坪 永和區-文化路 13,000 元/月
            </a>
          </article>
          <article class="item">
            <picture>
              <source srcset="//hp2.591.com.tw/house/second.webp 1x, //hp2.591.com.tw/house/second@2x.webp 2x">
            </picture>
            <a href="https://rent.591.com.tw/333444">
              永和近捷運獨立套房 獨立套房 7坪 永和區-竹林路 15,000 元/月
            </a>
          </article>
        </section>
        """

        listings = source._parse_public_html("https://rent.591.com.tw/list?kind=3&region=3&section=37", html)

        self.assertEqual(len(listings), 2)
        self.assertEqual(listings[0].listing_id, "111222")
        self.assertEqual(listings[0].image_urls, ["https://hp1.591.com.tw/house/bg.jpg"])
        self.assertEqual(listings[1].listing_id, "333444")
        self.assertEqual(listings[1].image_urls[0], "https://hp2.591.com.tw/house/second.webp")

    def test_detect_access_wall_error_codes(self) -> None:
        self.assertEqual(_detect_access_wall("<html>請完成 CAPTCHA 人機驗證</html>"), SourceErrorCode.CAPTCHA_DETECTED)
        self.assertEqual(_detect_access_wall("<html>請先登入後才能查看</html>"), SourceErrorCode.LOGIN_REQUIRED)
        self.assertEqual(_detect_access_wall("<html>Too Many Requests</html>"), SourceErrorCode.HTTP_RATE_LIMITED)
        self.assertEqual(_detect_access_wall("<html>403 Forbidden</html>"), SourceErrorCode.HTTP_FORBIDDEN)
        self.assertIsNone(_detect_access_wall("<html>正常公開搜尋結果</html>"))

    def test_paginated_search_urls_use_first_row_offsets(self) -> None:
        urls = _paginated_search_urls("https://rent.591.com.tw/list?kind=2&region=3&section=37", 3, 30)

        self.assertEqual(urls[0], "https://rent.591.com.tw/list?kind=2&region=3&section=37")
        self.assertEqual(urls[1], "https://rent.591.com.tw/list?kind=2&region=3&section=37&firstRow=30")
        self.assertEqual(urls[2], "https://rent.591.com.tw/list?kind=2&region=3&section=37&firstRow=60")

    def test_expand_dash_separated_sections_to_single_section_urls(self) -> None:
        urls = _expand_section_search_urls(
            ["https://rent.591.com.tw/list?kind=2&region=1&section=2-3-4&firstRow=60"]
        )

        self.assertEqual(
            urls,
            [
                "https://rent.591.com.tw/list?kind=2&region=1&section=2",
                "https://rent.591.com.tw/list?kind=2&region=1&section=3",
                "https://rent.591.com.tw/list?kind=2&region=1&section=4",
            ],
        )

    def test_extract_update_age_days(self) -> None:
        self.assertEqual(_extract_update_age_days("新上架 採光套房"), 0)
        self.assertEqual(_extract_update_age_days("仲介王先生 17小時內更新 昨日22人瀏覽"), 0)
        self.assertEqual(_extract_update_age_days("屋主 4天前更新 昨日128人瀏覽"), 4)
        self.assertEqual(_extract_update_age_days("昨日更新"), 1)
        self.assertIsNone(_extract_update_age_days("無更新時間文字"))


if __name__ == "__main__":
    unittest.main()
