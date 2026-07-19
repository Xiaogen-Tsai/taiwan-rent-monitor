from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from rent_bot.config import Settings
from rent_bot.filters import listing_has_female_only_restriction
from rent_bot.sources.base import SourceBlockedError, SourceErrorCode
from rent_bot.sources.rental_common import fingerprint_listing
from rent_bot.sources.source_houseprice import SourceHouseprice
from rent_bot.sources.source_rakuya import SourceRakuya
from rent_bot.sources.source_sinyi import SourceSinyi
from rent_bot.sources.source_yungching import SourceYungching


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakePage:
    def close(self) -> None:
        pass


class FakeContext:
    def set_default_timeout(self, _timeout: int) -> None:
        pass

    def new_page(self) -> FakePage:
        return FakePage()

    def close(self) -> None:
        pass


class FakeBrowser:
    def new_context(self, **_kwargs) -> FakeContext:  # noqa: ANN003
        return FakeContext()

    def close(self) -> None:
        pass


class FakeChromium:
    def launch(self, **_kwargs) -> FakeBrowser:  # noqa: ANN003
        return FakeBrowser()


class FakePlaywright:
    chromium = FakeChromium()


class FakeSyncPlaywright:
    def __enter__(self) -> FakePlaywright:
        return FakePlaywright()

    def __exit__(self, *_args) -> None:  # noqa: ANN002
        return None


def fake_sync_playwright() -> FakeSyncPlaywright:
    return FakeSyncPlaywright()


class PublicRentalSourceParserTest(unittest.TestCase):
    def test_rakuya_list_jsonld_parser(self) -> None:
        source = SourceRakuya(Settings())
        listings = source._parse_public_html("https://rent.rakuya.com.tw/result?keyword=套房", fixture("rakuya_list.html"))

        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.source, "rakuya")
        self.assertEqual(listing.listing_id, "0d2bec34544563b")
        self.assertEqual(listing.url, "https://community.rakuya.com.tw/43645/rent/0d2bec34544563b")
        self.assertEqual(listing.title, "大安森林公園旁獨立套房")
        self.assertEqual(listing.city, "台北市")
        self.assertEqual(listing.district, "大安區")
        self.assertEqual(listing.rent, 16800)
        self.assertEqual(listing.area_ping, 7.5)
        self.assertEqual(listing.room_type, "獨立套房")
        self.assertTrue(listing.has_rent_subsidy)
        self.assertTrue(listing.has_tax_registration)
        self.assertTrue(listing.has_independent_washer)
        self.assertTrue(listing.has_garbage_collection)
        self.assertEqual(listing.raw_json["update_age_days"], 0)

    def test_rakuya_detail_text_parser_marks_female_only(self) -> None:
        source = SourceRakuya(Settings())
        listings = source._parse_public_html(
            "https://community.rakuya.com.tw/43645/rent/0d2bec34544563b",
            fixture("rakuya_detail.html"),
        )

        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.district, "永和區")
        self.assertEqual(listing.rent, 15000)
        self.assertEqual(listing.area_ping, 6.5)
        self.assertIn("gender_restriction", listing.raw_json)
        self.assertTrue(listing_has_female_only_restriction(listing))

    def test_yungching_embedded_state_parser(self) -> None:
        source = SourceYungching(Settings())
        listings = source._parse_public_html("https://rent.yungching.com.tw/list/demo", fixture("yungching_list.html"))

        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.source, "yungching")
        self.assertEqual(listing.listing_id, "YC123456")
        self.assertEqual(listing.district, "大安區")
        self.assertEqual(listing.rent, 16000)
        self.assertEqual(listing.area_ping, 8.0)
        self.assertEqual(listing.room_type, "獨立套房")
        self.assertEqual(listing.raw_json["layout"], "1房1衛")
        self.assertTrue(listing.has_rent_subsidy)
        self.assertTrue(listing.has_tax_registration)
        self.assertTrue(listing.has_independent_washer)
        self.assertTrue(listing.has_garbage_collection)

    def test_yungching_detail_text_parser(self) -> None:
        source = SourceYungching(Settings())
        listings = source._parse_public_html(
            "https://rent.yungching.com.tw/house/YC987654",
            fixture("yungching_detail.html"),
        )

        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.district, "文山區")
        self.assertEqual(listing.rent, 12000)
        self.assertEqual(listing.area_ping, 5.5)
        self.assertEqual(listing.room_type, "分租套房")
        self.assertEqual(listing.raw_json["update_age_days"], 2)

    def test_houseprice_card_parser(self) -> None:
        source = SourceHouseprice(Settings())
        listings = source._parse_public_html("https://rent.houseprice.tw/", fixture("houseprice_list.html"))

        self.assertEqual(len(listings), 2)
        first = listings[0]
        self.assertEqual(first.source, "houseprice_5168")
        self.assertEqual(first.listing_id, "1421104_291203")
        self.assertEqual(first.url, "https://rent.houseprice.tw/house/1421104_291203")
        self.assertEqual(first.district, "大同區")
        self.assertEqual(first.rent, 16000)
        self.assertEqual(first.area_ping, 6.0)
        self.assertEqual(first.room_type, "獨立套房")
        self.assertTrue(first.has_rent_subsidy)
        self.assertTrue(first.has_tax_registration)
        self.assertTrue(first.has_independent_washer)
        self.assertTrue(first.has_garbage_collection)
        self.assertTrue(listing_has_female_only_restriction(listings[1]))

    def test_houseprice_detail_text_parser(self) -> None:
        source = SourceHouseprice(Settings())
        listings = source._parse_public_html(
            "https://rent.houseprice.tw/house/1419388_2341119",
            fixture("houseprice_detail.html"),
        )

        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.district, "松山區")
        self.assertEqual(listing.room_type, "整層住家")
        self.assertEqual(listing.area_ping, 8.22)

    def test_sinyi_jsonld_parser(self) -> None:
        source = SourceSinyi(Settings())
        listings = source._parse_public_html("https://www.sinyi.com.tw/rent", fixture("sinyi_list.html"))

        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.source, "sinyi")
        self.assertEqual(listing.listing_id, "R12345")
        self.assertEqual(listing.url, "https://www.sinyi.com.tw/rent/houseno/R12345")
        self.assertEqual(listing.district, "信義區")
        self.assertEqual(listing.rent, 18000)
        self.assertEqual(listing.area_ping, 7.0)
        self.assertEqual(listing.room_type, "獨立套房")

    def test_placeholder_and_malformed_html_do_not_crash(self) -> None:
        source = SourceRakuya(Settings())
        self.assertEqual(source._parse_public_html("https://rent.rakuya.com.tw/result", "<div id='app'></div>"), [])
        self.assertEqual(source._parse_public_html("https://rent.rakuya.com.tw/result", "<html><script>{bad"), [])

    def test_fetch_reports_blocked_source_without_crashing(self) -> None:
        settings = Settings(
            source_rakuya_enabled=True,
            source_rakuya_search_urls=["https://rent.rakuya.com.tw/result?keyword=套房"],
            request_min_delay_seconds=0,
            request_max_delay_seconds=0,
        )
        source = SourceRakuya(settings)
        source._fetch_rendered_html = lambda _context, _page, url: (_ for _ in ()).throw(  # type: ignore[method-assign]
            SourceBlockedError(SourceErrorCode.HTTP_FORBIDDEN, "HTTP 403", url)
        )

        with patch("rent_bot.sources.rental_common.sync_playwright", fake_sync_playwright):
            result = source.fetch()

        self.assertEqual(result.listings, [])
        self.assertEqual(len(result.errors), 1)
        self.assertIn("HTTP_FORBIDDEN", str(result.errors[0]))

    def test_fetch_reports_robots_unavailable_fail_closed(self) -> None:
        settings = Settings(
            source_houseprice_enabled=True,
            source_houseprice_search_urls=["https://rent.houseprice.tw/"],
            request_min_delay_seconds=0,
            request_max_delay_seconds=0,
        )
        source = SourceHouseprice(settings)
        source._fetch_rendered_html = lambda _context, _page, url: (_ for _ in ()).throw(  # type: ignore[method-assign]
            SourceBlockedError(SourceErrorCode.ROBOTS_UNAVAILABLE, "robots.txt unavailable; failing closed", url)
        )

        with patch("rent_bot.sources.rental_common.sync_playwright", fake_sync_playwright):
            result = source.fetch()

        self.assertEqual(result.listings, [])
        self.assertEqual(len(result.errors), 1)
        self.assertIn("ROBOTS_UNAVAILABLE", str(result.errors[0]))

    def test_fetch_reports_access_wall_errors_without_crashing(self) -> None:
        for code in (
            SourceErrorCode.HTTP_RATE_LIMITED,
            SourceErrorCode.CAPTCHA_DETECTED,
            SourceErrorCode.LOGIN_REQUIRED,
        ):
            with self.subTest(code=code):
                settings = Settings(
                    source_sinyi_enabled=True,
                    source_sinyi_search_urls=["https://www.sinyi.com.tw/rent"],
                    request_min_delay_seconds=0,
                    request_max_delay_seconds=0,
                )
                source = SourceSinyi(settings)
                source._fetch_rendered_html = lambda _context, _page, url, code=code: (_ for _ in ()).throw(  # type: ignore[method-assign]
                    SourceBlockedError(code, code.value, url)
                )

                with patch("rent_bot.sources.rental_common.sync_playwright", fake_sync_playwright):
                    result = source.fetch()

                self.assertEqual(result.listings, [])
                self.assertEqual(len(result.errors), 1)
                self.assertIn(code.value, str(result.errors[0]))

    def test_fingerprint_is_available_for_normalized_duplicates(self) -> None:
        source = SourceHouseprice(Settings())
        listings = source._parse_public_html("https://rent.houseprice.tw/", fixture("houseprice_list.html"))

        self.assertIsNotNone(fingerprint_listing(listings[0]))


if __name__ == "__main__":
    unittest.main()
