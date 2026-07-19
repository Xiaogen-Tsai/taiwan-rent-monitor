from __future__ import annotations

import unittest
from datetime import datetime, timezone

from rent_bot.cli import (
    _group_listings_by_district,
    _order_pending_notifications,
    _watch_runtime_settings,
    _watch_start_message,
    _watch_summary_message,
)
from rent_bot.config import Settings
from rent_bot.db import PendingNotification
from rent_bot.models import Listing


def listing(listing_id: str, district: str, city: str = "台北市") -> Listing:
    return Listing(
        source="fixture",
        listing_id=listing_id,
        url=f"https://example.com/{listing_id}",
        title=f"{district} 套房",
        city=city,
        district=district,
    )


class WatchNotificationTest(unittest.TestCase):
    def test_watch_start_message(self) -> None:
        self.assertEqual(
            _watch_start_message(datetime(2026, 7, 9, 10, 33)),
            "租屋監控準備開始執行（2026-07-09 10:33）。",
        )

    def test_watch_summary_message_for_full_batch(self) -> None:
        listings = [
            listing("daan-1", "大安區"),
            listing("daan-2", "大安區"),
            listing("xindian-1", "新店區", city="新北市"),
            listing("wanhua-1", "萬華區"),
        ]

        self.assertEqual(_watch_summary_message(listings, listings), "待通知 4 間：大安區2間、新店區1間、萬華區1間。")

    def test_watch_summary_message_for_truncated_batch(self) -> None:
        candidates = [
            listing("daan-1", "大安區"),
            listing("daan-2", "大安區"),
            listing("xindian-1", "新店區", city="新北市"),
            listing("wanhua-1", "萬華區"),
        ]
        to_notify = candidates[:2]

        self.assertEqual(
            _watch_summary_message(candidates, to_notify),
            "待通知 4 間：大安區2間、新店區1間、萬華區1間。這次依新房源、價格下降、其他更新的順序先傳 2 間：大安區2間。",
        )

    def test_group_listings_by_district_keeps_same_district_together(self) -> None:
        listings = [
            listing("daan-1", "大安區"),
            listing("wanhua-1", "萬華區"),
            listing("daan-2", "大安區"),
            listing("xindian-1", "新店區", city="新北市"),
            listing("wanhua-2", "萬華區"),
        ]

        grouped = _group_listings_by_district(listings)

        self.assertEqual([item.listing_id for item in grouped], ["daan-1", "daan-2", "wanhua-1", "wanhua-2", "xindian-1"])

    def test_watch_notify_limit_setting_default(self) -> None:
        self.assertEqual(Settings().watch_notify_limit, 50)

    def test_settings_include_new_requested_districts(self) -> None:
        settings = Settings()

        self.assertIn("新店區", settings.allowed_city_districts["新北市"])
        self.assertIn("萬華區", settings.allowed_city_districts["台北市"])

    def test_pending_notifications_prioritize_new_then_price_drop_then_other(self) -> None:
        new_listing = listing("new", "萬華區")
        new_listing.near_ntu_score = 10
        price_drop = listing("price", "大安區")
        price_drop.near_ntu_score = 100
        other_update = listing("other", "大安區")
        other_update.near_ntu_score = 100
        pending = [
            PendingNotification(other_update, ["新增租補標記"], 2),
            PendingNotification(price_drop, ["價格下降 18000 -> 16000"], 1),
            PendingNotification(new_listing, ["新房源"], 0),
        ]

        ordered = _order_pending_notifications(pending)

        self.assertEqual([item.listing.listing_id for item in ordered], ["new", "price", "other"])

    def test_summary_reports_notification_types(self) -> None:
        candidates = [listing("new", "大安區"), listing("price", "萬華區")]
        reasons = {
            ("fixture", "new"): ["新房源"],
            ("fixture", "price"): ["價格下降 18000 -> 16000"],
        }

        message = _watch_summary_message(candidates, candidates, change_reasons=reasons)

        self.assertIn("新房源 1、價格下降 1", message)

    def test_catch_up_uses_extra_591_page_after_a_missed_run(self) -> None:
        settings = Settings(
            crawl_interval_minutes=60,
            catch_up_grace_minutes=15,
            source_591_max_pages=1,
            source_591_catch_up_max_pages=2,
        )

        runtime = _watch_runtime_settings(
            settings,
            "2026-07-19T02:20:00+00:00",
            datetime(2026, 7, 19, 4, 16, tzinfo=timezone.utc),
        )

        self.assertEqual(runtime.source_591_max_pages, 2)
        self.assertEqual(settings.source_591_max_pages, 1)

    def test_normal_hourly_run_does_not_enable_catch_up(self) -> None:
        settings = Settings(
            crawl_interval_minutes=60,
            catch_up_grace_minutes=15,
            source_591_max_pages=1,
            source_591_catch_up_max_pages=2,
        )

        runtime = _watch_runtime_settings(
            settings,
            "2026-07-19T02:20:00+00:00",
            datetime(2026, 7, 19, 3, 16, tzinfo=timezone.utc),
        )

        self.assertIs(runtime, settings)
        self.assertEqual(runtime.source_591_max_pages, 1)


if __name__ == "__main__":
    unittest.main()
