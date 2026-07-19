from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from rent_bot.models import Listing, UpsertResult, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    city TEXT,
    district TEXT,
    address TEXT,
    rent INTEGER,
    total_monthly_cost INTEGER,
    area_ping REAL,
    room_type TEXT,
    floor TEXT,
    description TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    has_rent_subsidy INTEGER,
    has_tax_registration INTEGER,
    has_independent_washer INTEGER,
    has_garbage_collection INTEGER,
    near_ntu_score INTEGER,
    commute_minutes_to_ntu INTEGER,
    image_urls TEXT NOT NULL DEFAULT '[]',
    first_seen_at TEXT,
    last_seen_at TEXT,
    last_notified_at TEXT,
    notified_snapshot TEXT,
    status TEXT NOT NULL DEFAULT 'unknown',
    raw_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (source, listing_id)
);

CREATE INDEX IF NOT EXISTS idx_listings_last_seen ON listings(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_location ON listings(city, district);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
    if "has_garbage_collection" not in existing_columns:
        conn.execute("ALTER TABLE listings ADD COLUMN has_garbage_collection INTEGER")
    if "notified_snapshot" not in existing_columns:
        conn.execute("ALTER TABLE listings ADD COLUMN notified_snapshot TEXT")
        _backfill_notified_snapshots(conn)


def connect(path: Path) -> sqlite3.Connection:
    init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _bool_to_db(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _bool_from_db(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)


@dataclass
class PendingNotification:
    listing: Listing
    reasons: list[str]
    priority: int

    @property
    def change_reasons(self) -> list[str]:
        """Alias kept for callers that use the Discord notifier terminology."""

        return self.reasons


def _notification_snapshot_payload(listing: Listing) -> dict[str, object]:
    return {
        "rent": listing.rent,
        "total_monthly_cost": listing.total_monthly_cost,
        "has_rent_subsidy": listing.has_rent_subsidy,
        "has_tax_registration": listing.has_tax_registration,
        "has_independent_washer": listing.has_independent_washer,
        "has_garbage_collection": listing.has_garbage_collection,
        "status": listing.status,
    }


def _serialize_notification_snapshot(listing: Listing) -> str:
    return json.dumps(
        _notification_snapshot_payload(listing),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _backfill_notified_snapshots(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT rowid, rent, total_monthly_cost, has_rent_subsidy,
               has_tax_registration, has_independent_washer,
               has_garbage_collection, status
        FROM listings
        """
    ).fetchall()
    for row in rows:
        payload = {
            "rent": row[1],
            "total_monthly_cost": row[2],
            "has_rent_subsidy": _bool_from_db(row[3]),
            "has_tax_registration": _bool_from_db(row[4]),
            "has_independent_washer": _bool_from_db(row[5]),
            "has_garbage_collection": _bool_from_db(row[6]),
            "status": row[7] or "unknown",
        }
        snapshot = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        conn.execute(
            "UPDATE listings SET notified_snapshot = ? WHERE rowid = ?",
            (snapshot, row[0]),
        )


def _dt_to_db(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _dt_from_db(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def listing_from_row(row: sqlite3.Row) -> Listing:
    return Listing(
        source=row["source"],
        listing_id=row["listing_id"],
        url=row["url"],
        title=row["title"],
        city=row["city"],
        district=row["district"],
        address=row["address"],
        rent=row["rent"],
        total_monthly_cost=row["total_monthly_cost"],
        area_ping=row["area_ping"],
        room_type=row["room_type"],
        floor=row["floor"],
        description=row["description"] or "",
        tags=json.loads(row["tags"] or "[]"),
        has_rent_subsidy=_bool_from_db(row["has_rent_subsidy"]),
        has_tax_registration=_bool_from_db(row["has_tax_registration"]),
        has_independent_washer=_bool_from_db(row["has_independent_washer"]),
        has_garbage_collection=_bool_from_db(row["has_garbage_collection"]),
        near_ntu_score=row["near_ntu_score"],
        commute_minutes_to_ntu=row["commute_minutes_to_ntu"],
        image_urls=json.loads(row["image_urls"] or "[]"),
        first_seen_at=_dt_from_db(row["first_seen_at"]),
        last_seen_at=_dt_from_db(row["last_seen_at"]),
        last_notified_at=_dt_from_db(row["last_notified_at"]),
        status=row["status"] or "unknown",
        raw_json=json.loads(row["raw_json"] or "{}"),
    )


def get_listing(conn: sqlite3.Connection, source: str, listing_id: str) -> Listing | None:
    row = conn.execute(
        "SELECT * FROM listings WHERE source = ? AND listing_id = ?",
        (source, listing_id),
    ).fetchone()
    return listing_from_row(row) if row else None


def _listing_params(listing: Listing) -> tuple:
    return (
        listing.source,
        listing.listing_id,
        listing.url,
        listing.title,
        listing.city,
        listing.district,
        listing.address,
        listing.rent,
        listing.total_monthly_cost,
        listing.area_ping,
        listing.room_type,
        listing.floor,
        listing.description,
        json.dumps(listing.tags, ensure_ascii=False),
        _bool_to_db(listing.has_rent_subsidy),
        _bool_to_db(listing.has_tax_registration),
        _bool_to_db(listing.has_independent_washer),
        _bool_to_db(listing.has_garbage_collection),
        listing.near_ntu_score,
        listing.commute_minutes_to_ntu,
        json.dumps(listing.image_urls[:3], ensure_ascii=False),
        _dt_to_db(listing.first_seen_at),
        _dt_to_db(listing.last_seen_at),
        _dt_to_db(listing.last_notified_at),
        listing.status,
        json.dumps(listing.raw_json, ensure_ascii=False),
    )


def _important_changes(old: Listing, new: Listing) -> list[str]:
    changes: list[str] = []
    old_cost = old.total_monthly_cost or old.rent
    new_cost = new.total_monthly_cost or new.rent
    if old_cost and new_cost and new_cost < old_cost:
        changes.append(f"價格下降 {old_cost} -> {new_cost}")
    if old.has_rent_subsidy is not True and new.has_rent_subsidy is True:
        changes.append("新增租補標記")
    if old.has_tax_registration is not True and new.has_tax_registration is True:
        changes.append("新增可報稅標記")
    if old.has_independent_washer is not True and new.has_independent_washer is True:
        changes.append("新增獨立洗衣機資訊")
    if old.has_garbage_collection is not True and new.has_garbage_collection is True:
        changes.append("新增垃圾代收資訊")
    if old.status != "active" and new.status == "active":
        changes.append("重新上架")
    return changes


def _decode_notification_snapshot(value: str | None) -> dict[str, object] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _snapshot_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _snapshot_cost(snapshot: dict[str, object]) -> int | None:
    for key in ("total_monthly_cost", "rent"):
        value = snapshot.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
        if isinstance(value, str):
            try:
                parsed = int(value)
            except ValueError:
                continue
            if parsed > 0:
                return parsed
    return None


def _changes_since_notification(snapshot: dict[str, object], listing: Listing) -> list[str]:
    changes: list[str] = []
    old_cost = _snapshot_cost(snapshot)
    new_cost = listing.total_monthly_cost or listing.rent
    if old_cost and new_cost and new_cost < old_cost:
        changes.append(f"價格下降 {old_cost} -> {new_cost}")
    fields = (
        ("has_rent_subsidy", listing.has_rent_subsidy, "新增租補標記"),
        ("has_tax_registration", listing.has_tax_registration, "新增可報稅標記"),
        ("has_independent_washer", listing.has_independent_washer, "新增獨立洗衣機資訊"),
        ("has_garbage_collection", listing.has_garbage_collection, "新增垃圾代收資訊"),
    )
    for key, current_value, reason in fields:
        if _snapshot_bool(snapshot.get(key)) is not True and current_value is True:
            changes.append(reason)
    was_relisted = _snapshot_bool(listing.raw_json.get("notification_relisted")) is True
    if listing.status == "active" and (snapshot.get("status") != "active" or was_relisted):
        changes.append("重新上架")
    return changes


def _notification_is_suppressed(raw_json: dict) -> bool:
    value = raw_json.get("notification_suppressed")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return value is True or value == 1


def list_pending_notifications(conn: sqlite3.Connection) -> list[PendingNotification]:
    rows = conn.execute(
        """
        SELECT * FROM listings
        WHERE status = 'active'
        ORDER BY first_seen_at, source, listing_id
        """
    ).fetchall()
    pending: list[PendingNotification] = []
    for row in rows:
        listing = listing_from_row(row)
        if _notification_is_suppressed(listing.raw_json):
            continue
        snapshot = _decode_notification_snapshot(row["notified_snapshot"])
        if snapshot is None:
            pending.append(PendingNotification(listing=listing, reasons=["新房源"], priority=0))
            continue
        reasons = _changes_since_notification(snapshot, listing)
        if reasons:
            priority = 1 if reasons[0].startswith("價格下降 ") else 2
            pending.append(PendingNotification(listing=listing, reasons=reasons, priority=priority))
    pending.sort(key=lambda item: item.priority)
    return pending


def upsert_listing(
    conn: sqlite3.Connection, listing: Listing, now: datetime | None = None
) -> UpsertResult:
    now = now or utc_now()
    existing = get_listing(conn, listing.source, listing.listing_id)
    if existing is None:
        listing.first_seen_at = listing.first_seen_at or now
        listing.last_seen_at = now
        conn.execute(
            """
            INSERT INTO listings (
                source, listing_id, url, title, city, district, address, rent,
                total_monthly_cost, area_ping, room_type, floor, description,
                tags, has_rent_subsidy, has_tax_registration,
                has_independent_washer, has_garbage_collection, near_ntu_score,
                commute_minutes_to_ntu, image_urls, first_seen_at,
                last_seen_at, last_notified_at, status, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _listing_params(listing),
        )
        return UpsertResult(listing=listing, is_new=True, important_changes=[])

    listing.first_seen_at = existing.first_seen_at
    listing.last_seen_at = now
    listing.last_notified_at = existing.last_notified_at
    for pending_key in ("notification_suppressed", "notification_relisted"):
        if pending_key not in listing.raw_json and pending_key in existing.raw_json:
            listing.raw_json[pending_key] = existing.raw_json[pending_key]
    if existing.status != "active" and listing.status == "active":
        listing.raw_json["notification_relisted"] = True
    changes = _important_changes(existing, listing)
    conn.execute(
        """
        UPDATE listings SET
            url = ?, title = ?, city = ?, district = ?, address = ?, rent = ?,
            total_monthly_cost = ?, area_ping = ?, room_type = ?, floor = ?,
            description = ?, tags = ?, has_rent_subsidy = ?,
            has_tax_registration = ?, has_independent_washer = ?,
            has_garbage_collection = ?,
            near_ntu_score = ?, commute_minutes_to_ntu = ?, image_urls = ?,
            first_seen_at = ?, last_seen_at = ?, last_notified_at = ?,
            status = ?, raw_json = ?
        WHERE source = ? AND listing_id = ?
        """,
        (
            listing.url,
            listing.title,
            listing.city,
            listing.district,
            listing.address,
            listing.rent,
            listing.total_monthly_cost,
            listing.area_ping,
            listing.room_type,
            listing.floor,
            listing.description,
            json.dumps(listing.tags, ensure_ascii=False),
            _bool_to_db(listing.has_rent_subsidy),
            _bool_to_db(listing.has_tax_registration),
            _bool_to_db(listing.has_independent_washer),
            _bool_to_db(listing.has_garbage_collection),
            listing.near_ntu_score,
            listing.commute_minutes_to_ntu,
            json.dumps(listing.image_urls[:3], ensure_ascii=False),
            _dt_to_db(listing.first_seen_at),
            _dt_to_db(listing.last_seen_at),
            _dt_to_db(listing.last_notified_at),
            listing.status,
            json.dumps(listing.raw_json, ensure_ascii=False),
            listing.source,
            listing.listing_id,
        ),
    )
    return UpsertResult(listing=listing, is_new=False, important_changes=changes)


def mark_notified(
    conn: sqlite3.Connection,
    source: str,
    listing_id: str,
    notified_at: datetime | None = None,
) -> None:
    notified_at = notified_at or utc_now()
    listing = get_listing(conn, source, listing_id)
    if listing is None:
        return
    raw_json = dict(listing.raw_json)
    raw_json.pop("notification_relisted", None)
    conn.execute(
        """
        UPDATE listings
        SET last_notified_at = ?, notified_snapshot = ?, raw_json = ?
        WHERE source = ? AND listing_id = ?
        """,
        (
            _dt_to_db(notified_at),
            _serialize_notification_snapshot(listing),
            json.dumps(raw_json, ensure_ascii=False),
            source,
            listing_id,
        ),
    )


def get_app_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else None


def set_app_state(
    conn: sqlite3.Connection,
    key: str,
    value: str,
    updated_at: datetime | None = None,
) -> None:
    timestamp = _dt_to_db(updated_at or utc_now())
    conn.execute(
        """
        INSERT INTO app_state (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value, timestamp),
    )


def list_recent(conn: sqlite3.Connection, limit: int = 20) -> list[Listing]:
    rows = conn.execute(
        """
        SELECT * FROM listings
        ORDER BY last_seen_at DESC NULLS LAST, first_seen_at DESC NULLS LAST
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [listing_from_row(row) for row in rows]


def mark_filtered_out(conn: sqlite3.Connection, listings: Iterable[Listing]) -> None:
    for listing in listings:
        listing.status = "filtered_out"
        upsert_listing(conn, listing)
