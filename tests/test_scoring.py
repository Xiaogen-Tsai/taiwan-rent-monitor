from __future__ import annotations

import unittest
from unittest.mock import patch

from rent_bot.config import Settings
from rent_bot.models import Listing
from rent_bot.scoring import enrich_ntu_distance, fallback_near_ntu_score


def listing(
    *,
    address: str | None = None,
    title: str = "獨立套房",
    city: str | None = "台北市",
    district: str | None = "大安區",
    description: str = "",
    tags: list[str] | None = None,
) -> Listing:
    return Listing(
        source="test",
        listing_id="listing-1",
        url="https://example.test/listing-1",
        title=title,
        city=city,
        district=district,
        address=address,
        description=description,
        tags=tags or [],
    )


class FallbackNtuScoringTests(unittest.TestCase):
    def test_official_campus_edge_roads_score_from_exact_address(self) -> None:
        cases = [
            ("台北市大安區羅斯福路四段74號", "大安區", 96, "campus_edge:roosevelt_4"),
            ("台北市大安區舟山路1號", "大安區", 96, "campus_edge:zhoushan"),
            ("台北市大安區新生南路三段86巷", "大安區", 94, "campus_edge:xinsheng_south_3"),
            ("台北市大安區辛亥路二段170號", "大安區", 92, "campus_edge:xinhai_2"),
            ("台北市大安區基隆路四段144巷", "大安區", 90, "campus_edge:keelung_4"),
            ("台北市大安區長興街31號", "大安區", 90, "campus_gate:changxing"),
        ]

        for address, district, expected, basis in cases:
            with self.subTest(address=address):
                item = listing(address=address, district=district)
                self.assertEqual(fallback_near_ntu_score(item), expected)
                self.assertEqual(item.raw_json["ntu_score_method"], "address_road")
                self.assertEqual(item.raw_json["ntu_score_confidence"], "high")
                self.assertEqual(item.raw_json["ntu_score_basis"], f"road:{basis}")

    def test_walkable_streets_are_layered_below_campus_edges(self) -> None:
        wenzhou = listing(address="台北市大安區溫州街74巷")
        taishun = listing(address="台北市大安區泰順街16巷")
        tingzhou = listing(address="台北市中正區汀洲路三段284巷", district="中正區")

        self.assertEqual(fallback_near_ntu_score(wenzhou), 88)
        self.assertEqual(fallback_near_ntu_score(taishun), 86)
        self.assertEqual(fallback_near_ntu_score(tingzhou), 86)

    def test_common_tingzhou_typo_and_fullwidth_section_are_normalized(self) -> None:
        typo = listing(address="台北市中正區汀洲路三段284巷", district="中正區")
        fullwidth = listing(address="臺北市大安區羅斯福路４段７４號")

        self.assertEqual(fallback_near_ntu_score(typo), 86)
        self.assertEqual(fallback_near_ntu_score(fullwidth), 96)

    def test_roosevelt_section_six_and_near_ntu_marketing_do_not_score_high(self) -> None:
        item = listing(
            address="台北市文山區羅斯福路六段",
            # Simulate a source-level district conflict seen in stored data.
            district="大安區",
            title="近台大、近捷運的優質套房",
        )

        self.assertEqual(fallback_near_ntu_score(item), 40)
        self.assertEqual(item.raw_json["ntu_score_method"], "district")
        self.assertEqual(item.raw_json["ntu_score_basis"], "district:台北市/文山區")

    def test_generic_ntu_and_mrt_words_add_nothing(self) -> None:
        plain = listing(address=None, district="文山區", title="獨立套房")
        marketed = listing(
            address=None,
            district="文山區",
            title="近台大、近捷運、松山新店線生活圈",
        )

        self.assertEqual(fallback_near_ntu_score(plain), 40)
        self.assertEqual(fallback_near_ntu_score(marketed), 40)
        self.assertEqual(marketed.raw_json["ntu_score_method"], "district")

    def test_repeated_keyword_is_not_additive(self) -> None:
        once = listing(address=None, title="羅斯福路四段套房")
        repeated = listing(
            address=None,
            title="羅斯福路四段套房，羅斯福路四段，羅斯福路四段",
        )

        self.assertEqual(fallback_near_ntu_score(once), 78)
        self.assertEqual(fallback_near_ntu_score(repeated), 78)

    def test_context_road_is_lower_confidence_than_an_address(self) -> None:
        exact = listing(address="台北市大安區羅斯福路四段74號")
        context_only = listing(address=None, title="羅斯福路四段74號獨立套房")

        self.assertEqual(fallback_near_ntu_score(exact), 96)
        self.assertEqual(fallback_near_ntu_score(context_only), 78)
        self.assertEqual(exact.raw_json["ntu_score_confidence"], "high")
        self.assertEqual(context_only.raw_json["ntu_score_confidence"], "low")
        self.assertEqual(context_only.raw_json["ntu_score_method"], "context_road")

    def test_context_road_cannot_override_conflicting_structured_location(self) -> None:
        item = listing(
            address=None,
            city="新北市",
            district="永和區",
            title="台北市大安區羅斯福路四段，近台大",
        )

        self.assertEqual(fallback_near_ntu_score(item), 42)
        self.assertEqual(item.raw_json["ntu_score_method"], "district")
        self.assertEqual(item.raw_json["ntu_score_basis"], "district:新北市/永和區")

    def test_explicit_mrt_stations_are_layered_and_generic_mrt_is_ignored(self) -> None:
        gongguan = listing(address=None, title="公館站三分鐘")
        wanlong = listing(address=None, title="捷運萬隆站五分鐘")
        jingmei = listing(address=None, title="景美捷運站旁")
        dapinglin = listing(address=None, title="大坪林站附近")
        generic = listing(address=None, title="近捷運套房")

        self.assertEqual(fallback_near_ntu_score(gongguan), 78)
        self.assertEqual(fallback_near_ntu_score(wanlong), 70)
        self.assertEqual(fallback_near_ntu_score(jingmei), 64)
        self.assertEqual(fallback_near_ntu_score(dapinglin), 58)
        self.assertEqual(fallback_near_ntu_score(generic), 50)
        self.assertEqual(gongguan.raw_json["ntu_score_confidence"], "low")

    def test_station_in_address_is_stronger_than_station_in_context(self) -> None:
        address_station = listing(address="捷運公館站3號出口")
        context_station = listing(address=None, title="捷運公館站3號出口")

        self.assertEqual(fallback_near_ntu_score(address_station), 90)
        self.assertEqual(fallback_near_ntu_score(context_station), 78)
        self.assertEqual(address_station.raw_json["ntu_score_confidence"], "medium")
        self.assertEqual(context_station.raw_json["ntu_score_confidence"], "low")

    def test_precise_road_wins_over_station_marketing(self) -> None:
        item = listing(
            address="台北市大安區泰順街16巷",
            title="公館站三分鐘",
        )

        self.assertEqual(fallback_near_ntu_score(item), 86)
        self.assertEqual(item.raw_json["ntu_score_basis"], "road:walkable:taishun")

    def test_district_only_scores_are_conservative(self) -> None:
        daan = listing(address=None, district="大安區")
        yonghe = listing(address=None, city="新北市", district="永和區")
        datong = listing(address=None, district="大同區")

        self.assertEqual(fallback_near_ntu_score(daan), 50)
        self.assertEqual(fallback_near_ntu_score(yonghe), 42)
        self.assertEqual(fallback_near_ntu_score(datong), 18)
        self.assertEqual(daan.raw_json["ntu_score_confidence"], "low")

    def test_unknown_location_returns_none_instead_of_an_arbitrary_default(self) -> None:
        item = listing(
            address=None,
            city=None,
            district=None,
            title="近台大近捷運套房",
        )

        self.assertIsNone(fallback_near_ntu_score(item))
        self.assertEqual(item.raw_json["ntu_score_method"], "unknown")
        self.assertEqual(item.raw_json["ntu_score_confidence"], "none")


class EnrichNtuDistanceTests(unittest.TestCase):
    def test_disabled_ntu_ranking_clears_scores_and_skips_routes(self) -> None:
        item = listing(address="台北市大安區羅斯福路四段74號")
        item.near_ntu_score = 96
        item.commute_minutes_to_ntu = 12
        item.raw_json["ntu_score_method"] = "address_road"
        settings = Settings(
            enable_ntu_ranking=False,
            enable_google_maps=True,
            google_maps_api_key="test-key",
        )

        with patch("rent_bot.optional.google_maps.estimate_minutes_to_ntu") as estimate:
            result = enrich_ntu_distance(item, settings)

        self.assertIs(result, item)
        self.assertIsNone(item.near_ntu_score)
        self.assertIsNone(item.commute_minutes_to_ntu)
        self.assertFalse(item.raw_json["ntu_scoring_enabled"])
        self.assertNotIn("ntu_score_method", item.raw_json)
        estimate.assert_not_called()

    def test_google_routes_success_has_high_confidence_and_wins(self) -> None:
        item = listing(address="台北市大安區羅斯福路六段")
        settings = Settings(enable_google_maps=True, google_maps_api_key="test-key")

        with patch("rent_bot.optional.google_maps.estimate_minutes_to_ntu", return_value=12) as estimate:
            result = enrich_ntu_distance(item, settings)

        self.assertIs(result, item)
        self.assertEqual(item.commute_minutes_to_ntu, 12)
        self.assertEqual(item.near_ntu_score, 100)
        self.assertEqual(item.raw_json["ntu_score_method"], "google_routes")
        self.assertEqual(item.raw_json["ntu_score_confidence"], "high")
        self.assertTrue(item.raw_json["ntu_scoring_enabled"])
        estimate.assert_called_once()

    def test_google_routes_is_not_called_for_district_only_location(self) -> None:
        item = listing(address=None, district="大安區")
        settings = Settings(enable_google_maps=True, google_maps_api_key="test-key")

        with patch("rent_bot.optional.google_maps.estimate_minutes_to_ntu") as estimate:
            enrich_ntu_distance(item, settings)

        estimate.assert_not_called()
        self.assertEqual(item.near_ntu_score, 50)
        self.assertEqual(item.raw_json["ntu_score_method"], "district")


if __name__ == "__main__":
    unittest.main()
