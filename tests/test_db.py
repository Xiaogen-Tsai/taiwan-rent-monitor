from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from rent_bot.db import (
    SCHEMA,
    connect,
    get_app_state,
    get_listing,
    list_pending_notifications,
    mark_notified,
    set_app_state,
    upsert_listing,
)
from rent_bot.models import Listing


class DatabaseMigrationTest(unittest.TestCase):
    def test_adds_garbage_collection_column_to_legacy_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rent_bot.sqlite3"
            legacy_schema = SCHEMA.replace("    has_garbage_collection INTEGER,\n", "")
            conn = sqlite3.connect(path)
            try:
                conn.executescript(legacy_schema)
                conn.commit()
            finally:
                conn.close()

            conn = connect(path)
            try:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(listings)")}
            finally:
                conn.close()

            self.assertIn("has_garbage_collection", columns)

    def test_notified_snapshot_migration_backfills_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rent_bot.sqlite3"
            legacy_schema = SCHEMA.replace("    notified_snapshot TEXT,\n", "")
            conn = sqlite3.connect(path)
            try:
                conn.executescript(legacy_schema)
                conn.execute(
                    """
                    INSERT INTO listings (
                        source, listing_id, url, title, rent, total_monthly_cost,
                        has_rent_subsidy, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("fixture", "legacy-1", "https://example.com/legacy-1", "既有套房", 15000, 15000, 1, "active"),
                )
                conn.commit()
            finally:
                conn.close()

            conn = connect(path)
            try:
                row = conn.execute(
                    "SELECT last_notified_at, notified_snapshot FROM listings WHERE listing_id = 'legacy-1'"
                ).fetchone()
                pending = list_pending_notifications(conn)
            finally:
                conn.close()

            self.assertIsNone(row["last_notified_at"])
            snapshot = json.loads(row["notified_snapshot"])
            self.assertEqual(snapshot["total_monthly_cost"], 15000)
            self.assertTrue(snapshot["has_rent_subsidy"])
            self.assertEqual(pending, [])

    def test_round_trips_garbage_collection_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rent_bot.sqlite3"
            conn = connect(path)
            try:
                upsert_listing(
                    conn,
                    Listing(
                        source="fixture",
                        listing_id="garbage-1",
                        url="https://example.com/garbage-1",
                        title="台北市大安區 獨立套房",
                        city="台北市",
                        district="大安區",
                        has_garbage_collection=True,
                    ),
                )
                loaded = get_listing(conn, "fixture", "garbage-1")
            finally:
                conn.close()

            self.assertIsNotNone(loaded)
            self.assertTrue(loaded.has_garbage_collection)


class PendingNotificationTest(unittest.TestCase):
    def _listing(self, listing_id: str = "pending-1", **overrides) -> Listing:  # noqa: ANN003
        values = {
            "source": "fixture",
            "listing_id": listing_id,
            "url": f"https://example.com/{listing_id}",
            "title": "台北市大安區獨立套房",
            "city": "台北市",
            "district": "大安區",
            "rent": 18000,
            "total_monthly_cost": 18000,
            "area_ping": 8,
            "room_type": "獨立套房",
            "status": "active",
        }
        values.update(overrides)
        return Listing(**values)

    def test_new_listing_starts_pending_with_null_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "rent_bot.sqlite3")
            try:
                upsert_listing(conn, self._listing())
                row = conn.execute(
                    "SELECT notified_snapshot FROM listings WHERE source = 'fixture' AND listing_id = 'pending-1'"
                ).fetchone()
                pending = list_pending_notifications(conn)
            finally:
                conn.close()

        self.assertIsNone(row["notified_snapshot"])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].listing.listing_id, "pending-1")
        self.assertEqual(pending[0].reasons, ["新房源"])
        self.assertEqual(pending[0].priority, 0)

    def test_mark_notified_records_current_snapshot_and_cross_run_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rent_bot.sqlite3"
            conn = connect(path)
            try:
                upsert_listing(conn, self._listing())
                notified_at = datetime(2026, 7, 19, 1, 2, 3, tzinfo=timezone.utc)
                mark_notified(conn, "fixture", "pending-1", notified_at=notified_at)
                conn.commit()
            finally:
                conn.close()

            conn = connect(path)
            try:
                upsert_listing(
                    conn,
                    self._listing(
                        rent=16000,
                        total_monthly_cost=16000,
                        has_rent_subsidy=True,
                    ),
                )
                row = conn.execute(
                    "SELECT last_notified_at, notified_snapshot FROM listings WHERE listing_id = 'pending-1'"
                ).fetchone()
                pending = list_pending_notifications(conn)
            finally:
                conn.close()

        self.assertEqual(row["last_notified_at"], notified_at.isoformat())
        self.assertEqual(json.loads(row["notified_snapshot"])["total_monthly_cost"], 18000)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].priority, 1)
        self.assertEqual(pending[0].reasons, ["價格下降 18000 -> 16000", "新增租補標記"])

    def test_price_rebound_clears_unnotified_price_drop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "rent_bot.sqlite3")
            try:
                upsert_listing(conn, self._listing())
                mark_notified(conn, "fixture", "pending-1")
                upsert_listing(conn, self._listing(rent=15000, total_monthly_cost=15000))
                self.assertEqual(list_pending_notifications(conn)[0].priority, 1)

                upsert_listing(conn, self._listing(rent=19000, total_monthly_cost=19000))
                pending = list_pending_notifications(conn)
            finally:
                conn.close()

        self.assertEqual(pending, [])

    def test_relisted_state_stays_pending_until_notification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "rent_bot.sqlite3")
            try:
                upsert_listing(conn, self._listing())
                mark_notified(conn, "fixture", "pending-1")
                upsert_listing(conn, self._listing(status="filtered_out"))
                upsert_listing(conn, self._listing(status="active"))

                pending = list_pending_notifications(conn)
                self.assertEqual(pending[0].reasons, ["重新上架"])
                self.assertEqual(pending[0].priority, 2)

                upsert_listing(conn, self._listing(status="active"))
                self.assertEqual(list_pending_notifications(conn)[0].reasons, ["重新上架"])
                mark_notified(conn, "fixture", "pending-1")
                cleared = list_pending_notifications(conn)
            finally:
                conn.close()

        self.assertEqual(cleared, [])

    def test_pending_priority_is_new_then_price_then_other_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "rent_bot.sqlite3")
            try:
                upsert_listing(conn, self._listing("price-change"))
                mark_notified(conn, "fixture", "price-change")
                upsert_listing(
                    conn,
                    self._listing("price-change", rent=16000, total_monthly_cost=16000),
                )

                upsert_listing(conn, self._listing("amenity-change"))
                mark_notified(conn, "fixture", "amenity-change")
                upsert_listing(
                    conn,
                    self._listing(
                        "amenity-change",
                        has_rent_subsidy=True,
                        has_tax_registration=True,
                        has_independent_washer=True,
                        has_garbage_collection=True,
                    ),
                )

                upsert_listing(conn, self._listing("new-listing"))
                pending = list_pending_notifications(conn)
            finally:
                conn.close()

        self.assertEqual([item.priority for item in pending], [0, 1, 2])
        self.assertEqual(
            [item.listing.listing_id for item in pending],
            ["new-listing", "price-change", "amenity-change"],
        )
        self.assertEqual(
            pending[2].reasons,
            ["新增租補標記", "新增可報稅標記", "新增獨立洗衣機資訊", "新增垃圾代收資訊"],
        )

    def test_notification_suppression_is_respected_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "rent_bot.sqlite3")
            try:
                upsert_listing(conn, self._listing(raw_json={"notification_suppressed": True}))
                upsert_listing(conn, self._listing(raw_json={"parser": "fixture"}))
                pending = list_pending_notifications(conn)
                stored = get_listing(conn, "fixture", "pending-1")
            finally:
                conn.close()

        self.assertEqual(pending, [])
        self.assertTrue(stored.raw_json["notification_suppressed"])


class AppStateTest(unittest.TestCase):
    def test_round_trips_app_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "rent_bot.sqlite3")
            try:
                self.assertIsNone(get_app_state(conn, "last_completed_watch_at"))
                set_app_state(conn, "last_completed_watch_at", "2026-07-19T04:20:00+00:00")
                conn.commit()
                self.assertEqual(
                    get_app_state(conn, "last_completed_watch_at"),
                    "2026-07-19T04:20:00+00:00",
                )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
