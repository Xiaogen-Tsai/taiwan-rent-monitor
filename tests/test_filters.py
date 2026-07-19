from __future__ import annotations

import unittest

from rent_bot.config import Settings
from rent_bot.filters import apply_classification, keyword_classify, listing_matches
from rent_bot.models import Listing


class ListingFilterTest(unittest.TestCase):
    def test_accepts_matching_suite_listing(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="ok-1",
            url="https://example.com/ok-1",
            title="台北市大安區 近公館獨立套房",
            city="台北市",
            district="大安區",
            rent=18_000,
            total_monthly_cost=18_000,
            area_ping=6.5,
            room_type="獨立套房",
            description="可租補 可報稅 獨洗 垃圾代收",
        )

        listing = apply_classification(listing, keyword_classify(listing))
        result = listing_matches(listing, Settings())

        self.assertTrue(result.ok, result.reasons)
        self.assertTrue(listing.has_rent_subsidy)
        self.assertTrue(listing.has_tax_registration)
        self.assertTrue(listing.has_independent_washer)
        self.assertTrue(listing.has_garbage_collection)

    def test_garbage_collection_negative(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="no-garbage",
            url="https://example.com/no-garbage",
            title="台北市大安區 獨立套房",
            city="台北市",
            district="大安區",
            rent=15_000,
            total_monthly_cost=15_000,
            area_ping=6,
            room_type="獨立套房",
            description="自行倒垃圾",
        )

        listing = apply_classification(listing, keyword_classify(listing))

        self.assertFalse(listing.has_garbage_collection)

    def test_rejects_female_only_listing(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="female-only",
            url="https://example.com/female-only",
            title="台北市大安區 獨立套房 限女",
            city="台北市",
            district="大安區",
            rent=15_000,
            total_monthly_cost=15_000,
            area_ping=6,
            room_type="獨立套房",
        )

        result = listing_matches(listing, Settings())

        self.assertFalse(result.ok)
        self.assertTrue(any("限女" in reason for reason in result.reasons))

    def test_rejects_full_female_only_rent_phrase(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="female-only-rent-phrase",
            url="https://example.com/female-only-rent-phrase",
            title="台北市大安區 獨立套房",
            city="台北市",
            district="大安區",
            rent=15_000,
            total_monthly_cost=15_000,
            area_ping=6,
            room_type="獨立套房",
            description="此房屋限女生租住",
        )

        result = listing_matches(listing, Settings())

        self.assertFalse(result.ok)
        self.assertTrue(any("限女" in reason for reason in result.reasons))

    def test_rejects_female_only_exclusive_phrase(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="female-only-exclusive",
            url="https://example.com/female-only-exclusive",
            title="新店區 獨立套房 女性專屬",
            city="新北市",
            district="新店區",
            rent=8_500,
            total_monthly_cost=8_500,
            area_ping=6,
            room_type="獨立套房",
        )

        result = listing_matches(listing, Settings())

        self.assertFalse(result.ok)
        self.assertTrue(any("限女" in reason for reason in result.reasons))

    def test_rejects_female_only_gender_field(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="female-only-field",
            url="https://example.com/female-only-field",
            title="台北市大安區 獨立套房",
            city="台北市",
            district="大安區",
            rent=15_000,
            total_monthly_cost=15_000,
            area_ping=6,
            room_type="獨立套房",
            raw_json={"gender": "此房屋限女生租住"},
        )

        result = listing_matches(listing, Settings())

        self.assertFalse(result.ok)
        self.assertTrue(any("限女" in reason for reason in result.reasons))

    def test_accepts_gender_neutral_field(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="gender-neutral",
            url="https://example.com/gender-neutral",
            title="台北市大安區 獨立套房",
            city="台北市",
            district="大安區",
            rent=15_000,
            total_monthly_cost=15_000,
            area_ping=6,
            room_type="獨立套房",
            raw_json={"gender": "男女不限"},
        )

        result = listing_matches(listing, Settings())

        self.assertTrue(result.ok, result.reasons)

    def test_accepts_not_gender_restricted_phrase(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="gender-neutral-wording",
            url="https://example.com/gender-neutral-wording",
            title="台北市大安區 獨立套房",
            city="台北市",
            district="大安區",
            rent=15_000,
            total_monthly_cost=15_000,
            area_ping=6,
            room_type="獨立套房",
            description="性別不限女性男性皆可",
        )

        result = listing_matches(listing, Settings())

        self.assertTrue(result.ok, result.reasons)

    def test_rejects_area_below_six_ping(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="small-area",
            url="https://example.com/small-area",
            title="台北市大安區 獨立套房",
            city="台北市",
            district="大安區",
            rent=15_000,
            total_monthly_cost=15_000,
            area_ping=5.5,
            room_type="獨立套房",
        )

        result = listing_matches(listing, Settings())

        self.assertFalse(result.ok)
        self.assertTrue(any("坪數小於 6.0" in reason for reason in result.reasons))

    def test_rejects_excluded_room_type(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="room",
            url="https://example.com/room",
            title="台北市大安區 雅房",
            city="台北市",
            district="大安區",
            rent=8_000,
            total_monthly_cost=8_000,
            area_ping=6,
            room_type="雅房",
        )

        result = listing_matches(listing, Settings())

        self.assertFalse(result.ok)
        self.assertTrue(any("雅房" in reason for reason in result.reasons))

    def test_can_allow_female_only_listings_from_user_config(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="female-configurable",
            url="https://example.com/female-configurable",
            title="台北市大安區 獨立套房 限女",
            city="台北市",
            district="大安區",
            rent=15_000,
            area_ping=6,
            room_type="獨立套房",
        )

        result = listing_matches(listing, Settings(exclude_female_only=False))

        self.assertTrue(result.ok, result.reasons)

    def test_can_disable_explicit_suite_requirement_from_user_config(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="room-type-configurable",
            url="https://example.com/room-type-configurable",
            title="台北市大安區 一房一廳",
            city="台北市",
            district="大安區",
            rent=15_000,
            area_ping=8,
            room_type="一房一廳",
        )

        result = listing_matches(listing, Settings(suite_only=False))

        self.assertTrue(result.ok, result.reasons)

    def test_whole_city_wildcard_accepts_any_district_in_that_city(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="whole-city",
            url="https://example.com/whole-city",
            title="高雄市鼓山區 獨立套房",
            city="高雄市",
            district="鼓山區",
            rent=15_000,
            area_ping=8,
            room_type="獨立套房",
        )

        result = listing_matches(
            listing,
            Settings(allowed_city_districts={"高雄市": {"*"}}),
        )

        self.assertTrue(result.ok, result.reasons)

    def test_tai_spelling_is_normalized_during_location_filtering(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="tai-spelling",
            url="https://example.com/tai-spelling",
            title="臺中市西屯區 獨立套房",
            city="臺中市",
            district="西屯區",
            rent=15_000,
            area_ping=8,
            room_type="獨立套房",
        )

        result = listing_matches(
            listing,
            Settings(allowed_city_districts={"台中市": {"西屯區"}}),
        )

        self.assertTrue(result.ok, result.reasons)


if __name__ == "__main__":
    unittest.main()
