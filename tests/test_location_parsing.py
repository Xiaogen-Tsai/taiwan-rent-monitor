from __future__ import annotations

import unittest
from unittest.mock import Mock

from rent_bot.config import Settings
from rent_bot.models import Listing
from rent_bot.sources.rental_common import (
    PublicRentalSourceSpec,
    _extract_address_from_text,
    _extract_location,
    _listing_from_mapping,
)
from rent_bot.sources.source_ptt import (
    SourcePTT,
    _extract_location as extract_ptt_location,
    _maybe_relevant_text as ptt_relevant_text,
    _maybe_relevant_title as ptt_relevant_title,
)


class PublicSourceTaiwanLocationTest(unittest.TestCase):
    def test_listing_model_normalizes_tai_spelling_for_every_city(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="tai-spelling",
            url="https://example.test/tai-spelling",
            title="套房",
            city="臺中市",
            district="西屯區",
        )

        self.assertEqual(listing.city, "台中市")

    def test_structured_city_scopes_a_duplicate_district(self) -> None:
        self.assertEqual(
            _extract_location(
                "景觀套房",
                preferred_city="臺中市",
                preferred_district="大安區",
            ),
            ("台中市", "大安區"),
        )

    def test_full_city_in_text_scopes_short_duplicate_districts(self) -> None:
        self.assertEqual(_extract_location("臺南市東區東門路套房"), ("台南市", "東區"))
        self.assertEqual(_extract_location("基隆市中正區義一路套房"), ("基隆市", "中正區"))

    def test_ambiguous_district_without_city_is_not_guessed(self) -> None:
        self.assertEqual(_extract_location("大安區採光套房"), (None, None))
        self.assertEqual(_extract_location("東區近車站套房"), (None, None))

    def test_structured_city_is_not_overridden_by_a_conflicting_district(self) -> None:
        self.assertEqual(
            _extract_location(
                "景觀套房",
                preferred_city="台北市",
                preferred_district="西屯區",
            ),
            ("台北市", None),
        )

    def test_unique_district_can_identify_its_city(self) -> None:
        self.assertEqual(_extract_location("西屯區套房出租"), ("台中市", "西屯區"))
        self.assertEqual(_extract_location("苓雅區整層出租"), ("高雄市", "苓雅區"))

    def test_structured_mapping_uses_city_and_district_fields(self) -> None:
        spec = PublicRentalSourceSpec(
            name="example",
            enabled_attr="unused_enabled",
            search_urls_attr="unused_urls",
            max_pages_attr="unused_pages",
            max_age_days_attr="unused_age",
            detail_url_patterns=(r"example\.com/house/([0-9]+)",),
        )
        listing = _listing_from_mapping(
            spec,
            "https://example.com/search",
            {
                "id": "1",
                "url": "https://example.com/house/1",
                "title": "採光套房",
                "description": "近市場，今日更新",
                "city": "臺中市",
                "district": "大安區",
                "address": "臺中市大安區中山南路",
                "price": "12000",
            },
            "test",
        )

        self.assertIsNotNone(listing)
        assert listing is not None
        self.assertEqual((listing.city, listing.district), ("台中市", "大安區"))
        self.assertEqual(listing.address, "臺中市大安區中山南路")

    def test_address_extraction_supports_all_taiwan_and_normalizes_tai(self) -> None:
        self.assertEqual(
            _extract_address_from_text("物件地址：臺南市東區東門路二段", "台南市", "東區"),
            "台南市東區東門路二段",
        )


class PttRentTaoLocationTest(unittest.TestCase):
    def test_board_title_accepts_taoyuan_area_only(self) -> None:
        self.assertTrue(ptt_relevant_title("[無/桃園/大園] 客運園區分租套房"))
        self.assertFalse(ptt_relevant_title("[無/台北/大安] 捷運套房"))

    def test_extracts_modern_and_legacy_taoyuan_addresses(self) -> None:
        self.assertEqual(
            extract_ptt_location("地址：桃園市中壢區長沙路"),
            ("桃園市", "中壢區"),
        )
        self.assertEqual(
            extract_ptt_location("租屋地址：桃園縣龜山鄉明成街"),
            ("桃園市", "龜山區"),
        )

    def test_bracket_area_aliases_are_taoyuan_scoped(self) -> None:
        self.assertEqual(
            extract_ptt_location("[無/桃園/南崁] 近交流道套房"),
            ("桃園市", "蘆竹區"),
        )
        self.assertEqual(
            extract_ptt_location("[無/桃園/內壢] 火車站旁套房"),
            ("桃園市", "中壢區"),
        )

    def test_ambiguous_taoyuan_area_is_not_guessed(self) -> None:
        self.assertEqual(extract_ptt_location("[無/桃園/後站] 套房出租"), (None, None))

    def test_explicit_out_of_region_address_is_rejected(self) -> None:
        text = "[無/桃園/龜山] 套房出租\n地址：新北市新莊區中正路"
        self.assertEqual(extract_ptt_location(text), (None, None))
        self.assertFalse(ptt_relevant_text(text))

    def test_non_listing_request_is_rejected(self) -> None:
        self.assertFalse(ptt_relevant_text("[無/桃園/中壢] 求租套房"))

    def test_fetch_skips_board_when_taoyuan_is_not_selected(self) -> None:
        source = SourcePTT(
            Settings(
                source_ptt_enabled=True,
                allowed_city_districts={"台北市": {"大安區"}},
            )
        )
        source.polite_get = Mock()  # type: ignore[method-assign]

        result = source.fetch()

        self.assertEqual(result.listings, [])
        self.assertEqual(result.errors, [])
        source.polite_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
