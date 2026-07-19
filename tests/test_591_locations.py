from __future__ import annotations

import unittest

from rent_bot.config import Settings
from rent_bot.sources.source_591 import Source591
from rent_bot.taiwan_591_locations import (
    SECTION_BY_REGION_ID,
    TAIWAN_591_LOCATIONS,
    build_591_search_urls,
    city_for_district,
    normalize_and_validate_locations,
)


class Taiwan591LocationsTest(unittest.TestCase):
    def test_snapshot_covers_all_591_regions_and_sections(self) -> None:
        self.assertEqual(len(TAIWAN_591_LOCATIONS), 22)
        self.assertEqual(sum(len(districts) for _region, districts in TAIWAN_591_LOCATIONS.values()), 368)
        section_ids = [
            section_id
            for _region, districts in TAIWAN_591_LOCATIONS.values()
            for section_id in districts.values()
        ]
        self.assertEqual(len(section_ids), len(set(section_ids)))
        self.assertNotIn("東沙", TAIWAN_591_LOCATIONS["連江縣"][1])
        self.assertNotIn("南沙", TAIWAN_591_LOCATIONS["連江縣"][1])
        self.assertEqual(SECTION_BY_REGION_ID["26"]["256"], "東沙")
        self.assertEqual(SECTION_BY_REGION_ID["26"]["257"], "南沙")

    def test_builds_one_url_per_selected_district_and_room_type(self) -> None:
        urls = build_591_search_urls(
            {"台北市": {"文山區"}, "高雄市": {"鼓山區"}},
            kinds=(2, 3),
        )

        self.assertEqual(
            urls,
            [
                "https://rent.591.com.tw/list?kind=2&region=1&section=12",
                "https://rent.591.com.tw/list?kind=3&region=1&section=12",
                "https://rent.591.com.tw/list?kind=2&region=17&section=247",
                "https://rent.591.com.tw/list?kind=3&region=17&section=247",
            ],
        )

    def test_tai_spelling_is_normalized_for_city_and_district(self) -> None:
        locations = normalize_and_validate_locations(
            {"臺中市": ["西屯區"], "雲林縣": ["臺西鄉"], "新竹縣": ["峨眉鄉"]}
        )

        self.assertEqual(
            locations,
            {"台中市": {"西屯區"}, "雲林縣": {"台西鄉"}, "新竹縣": {"峨眉鄉"}},
        )

    def test_duplicate_district_names_are_scoped_by_city(self) -> None:
        self.assertIsNone(city_for_district("東區"))
        self.assertEqual(city_for_district("東區", preferred_city="臺中市"), "台中市")
        with self.assertRaisesRegex(ValueError, "Unknown district.*西屯區"):
            normalize_and_validate_locations({"台北市": ["西屯區"]})

    def test_wildcard_builds_whole_city_urls(self) -> None:
        urls = build_591_search_urls({"臺南市": ["*"]}, kinds=(2, 3))

        self.assertEqual(
            urls,
            [
                "https://rent.591.com.tw/list?kind=2&region=15",
                "https://rent.591.com.tw/list?kind=3&region=15",
            ],
        )

    def test_wildcard_must_be_used_by_itself(self) -> None:
        with self.assertRaisesRegex(ValueError, "Use '\\*' by itself"):
            build_591_search_urls({"台北市": ["*", "大安區"]})

    def test_large_auto_generation_has_clear_limit(self) -> None:
        all_districts = {
            city: list(districts)
            for city, (_region, districts) in TAIWAN_591_LOCATIONS.items()
        }

        with self.assertRaisesRegex(ValueError, r"generate 736 URLs.*Use \['\*'\]"):
            build_591_search_urls(all_districts)

    def test_explicit_urls_are_an_advanced_override(self) -> None:
        source = Source591(
            Settings(
                source_591_search_urls=["https://example.com/custom"],
                allowed_city_districts={"not-a-city": {"not-a-district"}},
            )
        )

        self.assertEqual(source._search_urls(), ["https://example.com/custom"])

    def test_auto_urls_respect_user_friendly_room_types(self) -> None:
        source = Source591(
            Settings(
                source_591_room_types=["獨立套房"],
                allowed_city_districts={"高雄市": {"鼓山區"}},
            )
        )

        self.assertEqual(
            source._search_urls(),
            ["https://rent.591.com.tw/list?kind=2&region=17&section=247"],
        )


if __name__ == "__main__":
    unittest.main()
