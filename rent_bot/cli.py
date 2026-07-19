from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from rent_bot.config import Settings, get_settings
from rent_bot.db import (
    PendingNotification,
    connect,
    get_app_state,
    list_pending_notifications,
    list_recent,
    mark_notified,
    set_app_state,
    upsert_listing,
)
from rent_bot.filters import apply_classification, keyword_classify, listing_matches
from rent_bot.models import Classification, Listing, UpsertResult, utc_now
from rent_bot.notifier_discord import send_listing_embeds_detailed, send_test_message, send_text_message
from rent_bot.scoring import enrich_ntu_distance, rank_score
from rent_bot.sources import Source591, SourceHouseprice, SourcePTT, SourceRakuya, SourceSinyi, SourceYungching
from rent_bot.sources.base import SourceResult
from rent_bot.sources.rental_common import fingerprint_listing
from rent_bot.taiwan_591_locations import TAIWAN_591_LOCATIONS


logger = logging.getLogger(__name__)

LAST_COMPLETED_WATCH_KEY = "last_completed_watch_at"


@dataclass
class ProcessedListing:
    listing: Listing
    upsert: UpsertResult


def main(argv: list[str] | None = None) -> None:
    configure_stdio()
    configure_logging()
    settings = get_settings()
    parser = argparse.ArgumentParser(description="台灣租屋監控 Discord 通知系統")
    subparsers = parser.add_subparsers(dest="command", required=False)
    subparsers.add_parser("backfill", help="抓目前房源，推送排名最高的前 N 筆")
    subparsers.add_parser("watch", help="抓新房源與重要更新")
    subparsers.add_parser("locations", help="列出可填入設定檔的縣市與 591 站內分區")
    subparsers.add_parser("test-discord", help="送一則 Discord webhook 測試訊息")
    test_591_parser = subparsers.add_parser("test-591", help="測試 591 source，不寫 DB、不送 Discord")
    test_591_parser.add_argument("--url", action="append", help="覆蓋 SOURCE_591_SEARCH_URLS，可重複指定")
    test_rakuya_parser = subparsers.add_parser("test-rakuya", help="測試 Rakuya source，不寫 DB、不送 Discord")
    test_rakuya_parser.add_argument("--url", action="append", help="覆蓋 SOURCE_RAKUYA_SEARCH_URLS，可重複指定")
    test_yungching_parser = subparsers.add_parser("test-yungching", help="測試 Yungching source，不寫 DB、不送 Discord")
    test_yungching_parser.add_argument("--url", action="append", help="覆蓋 SOURCE_YUNGCHING_SEARCH_URLS，可重複指定")
    test_houseprice_parser = subparsers.add_parser("test-houseprice", help="測試 5168 source，不寫 DB、不送 Discord")
    test_houseprice_parser.add_argument("--url", action="append", help="覆蓋 SOURCE_HOUSEPRICE_SEARCH_URLS，可重複指定")
    test_sinyi_parser = subparsers.add_parser("test-sinyi", help="測試 Sinyi source，不寫 DB、不送 Discord")
    test_sinyi_parser.add_argument("--url", action="append", help="覆蓋 SOURCE_SINYI_SEARCH_URLS，可重複指定")
    list_parser = subparsers.add_parser("list", help="列出最近看到的房源")
    list_parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    command = args.command or settings.run_mode

    if command == "test-discord":
        ok = send_test_message(settings)
        raise SystemExit(0 if ok else 1)
    if command == "locations":
        print_locations()
        return
    if command == "test-591":
        test_591_command(settings, urls=args.url or None)
        return
    if command == "test-rakuya":
        settings.source_rakuya_enabled = True
        if args.url:
            settings.source_rakuya_search_urls = args.url
        test_source_command("rakuya", SourceRakuya(settings).fetch())
        return
    if command == "test-yungching":
        settings.source_yungching_enabled = True
        if args.url:
            settings.source_yungching_search_urls = args.url
        test_source_command("yungching", SourceYungching(settings).fetch())
        return
    if command == "test-houseprice":
        settings.source_houseprice_enabled = True
        if args.url:
            settings.source_houseprice_search_urls = args.url
        test_source_command("houseprice_5168", SourceHouseprice(settings).fetch())
        return
    if command == "test-sinyi":
        settings.source_sinyi_enabled = True
        if args.url:
            settings.source_sinyi_search_urls = args.url
        test_source_command("sinyi", SourceSinyi(settings).fetch())
        return
    if command == "list":
        list_command(settings, limit=args.limit)
        return
    if command == "backfill":
        run_backfill(settings)
        return
    if command == "watch":
        run_watch(settings)
        return
    parser.error(f"Unknown command: {command}")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def print_locations() -> None:
    for city, (_region_id, districts) in TAIWAN_591_LOCATIONS.items():
        print(f"{city}：{'、'.join(districts)}")


def run_backfill(settings: Settings) -> None:
    logger.info("Starting backfill")
    with connect(settings.database_path) as conn:
        processed = process_sources(settings, conn)
        matched = [item.listing for item in processed]
        ranked = sorted(matched, key=lambda listing: rank_score(listing, settings.max_rent), reverse=True)
        notify_limit = settings.max_results_per_run
        to_notify = _group_listings_by_district(ranked[:notify_limit])
        sent_listings = send_listing_embeds_detailed(settings, to_notify)
        for listing in sent_listings:
            mark_notified(conn, listing.source, listing.listing_id)
        conn.commit()
        logger.info("Backfill complete: matched=%s notified=%s", len(matched), len(sent_listings))


def run_watch(settings: Settings) -> None:
    logger.info("Starting watch")
    send_text_message(settings, _watch_start_message())
    started_at = utc_now()
    with connect(settings.database_path) as conn:
        runtime_settings = _watch_runtime_settings(
            settings,
            get_app_state(conn, LAST_COMPLETED_WATCH_KEY),
            started_at,
        )
        process_sources(runtime_settings, conn)
        pending = _order_pending_notifications(
            list_pending_notifications(conn),
            max_rent=settings.max_rent,
        )
        candidates = [item.listing for item in pending]
        change_reasons = {
            (item.listing.source, item.listing.listing_id): item.reasons for item in pending
        }

        notify_limit = min(settings.watch_notify_limit, settings.max_results_per_run)
        to_notify = candidates[:notify_limit]
        if to_notify:
            send_text_message(
                settings,
                _watch_summary_message(candidates, to_notify, change_reasons=change_reasons),
            )
        sent_listings = send_listing_embeds_detailed(
            settings,
            to_notify,
            change_reasons=change_reasons,
        )
        for listing in sent_listings:
            mark_notified(conn, listing.source, listing.listing_id)
        completed_at = utc_now()
        set_app_state(conn, LAST_COMPLETED_WATCH_KEY, completed_at.isoformat(), updated_at=completed_at)
        conn.commit()
        logger.info(
            "Watch complete: pending=%s attempted=%s notified=%s",
            len(candidates),
            len(to_notify),
            len(sent_listings),
        )


def _watch_start_message(now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    return f"租屋監控準備開始執行（{current.strftime('%Y-%m-%d %H:%M')}）。"


def _watch_summary_message(
    candidates: list[Listing],
    to_notify: list[Listing],
    change_reasons: dict[tuple[str, str], list[str]] | None = None,
) -> str:
    candidate_count = len(candidates)
    sending_count = len(to_notify)
    candidate_counts = _format_district_counts(candidates)
    event_counts = _format_event_counts(candidates, change_reasons) if change_reasons else ""
    event_suffix = f"（{event_counts}）" if event_counts else ""
    if candidate_count > sending_count:
        sending_counts = _format_district_counts(to_notify)
        return (
            f"待通知 {candidate_count} 間{event_suffix}：{candidate_counts}。"
            f"這次依新房源、價格下降、其他更新的順序先傳 {sending_count} 間：{sending_counts}。"
        )
    return f"待通知 {candidate_count} 間{event_suffix}：{candidate_counts}。"


def _format_event_counts(
    listings: list[Listing],
    change_reasons: dict[tuple[str, str], list[str]],
) -> str:
    counts = {"新房源": 0, "價格下降": 0, "其他更新": 0}
    for listing in listings:
        reasons = change_reasons.get((listing.source, listing.listing_id), [])
        if "新房源" in reasons:
            counts["新房源"] += 1
        elif any(reason.startswith("價格下降 ") for reason in reasons):
            counts["價格下降"] += 1
        else:
            counts["其他更新"] += 1
    return "、".join(f"{label} {count}" for label, count in counts.items() if count)


def _order_pending_notifications(
    pending: list[PendingNotification],
    max_rent: int = 18_600,
) -> list[PendingNotification]:
    ordered: list[PendingNotification] = []
    for priority in sorted({item.priority for item in pending}):
        priority_items = [item for item in pending if item.priority == priority]
        priority_items.sort(
            key=lambda item: rank_score(item.listing, max_rent),
            reverse=True,
        )
        by_key = {
            (item.listing.source, item.listing.listing_id): item for item in priority_items
        }
        grouped = _group_listings_by_district([item.listing for item in priority_items])
        ordered.extend(by_key[(listing.source, listing.listing_id)] for listing in grouped)
    return ordered


def _watch_runtime_settings(
    settings: Settings,
    last_completed_value: str | None,
    now: datetime,
) -> Settings:
    last_completed = _parse_state_datetime(last_completed_value)
    if last_completed is None:
        return settings
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    elapsed_minutes = max(0.0, (now - last_completed).total_seconds() / 60)
    catch_up_threshold = settings.crawl_interval_minutes + settings.catch_up_grace_minutes
    if elapsed_minutes <= catch_up_threshold:
        return settings

    expected_intervals = max(
        2,
        math.ceil(
            (elapsed_minutes - settings.catch_up_grace_minutes)
            / settings.crawl_interval_minutes
        ),
    )
    catch_up_pages = min(settings.source_591_catch_up_max_pages, expected_intervals)
    catch_up_pages = max(settings.source_591_max_pages, catch_up_pages)
    if catch_up_pages == settings.source_591_max_pages:
        return settings

    runtime_settings = settings.model_copy(deep=True)
    runtime_settings.source_591_max_pages = catch_up_pages
    logger.info(
        "Catch-up crawl enabled after %.0f minutes: 591 pages per search %s -> %s",
        elapsed_minutes,
        settings.source_591_max_pages,
        catch_up_pages,
    )
    return runtime_settings


def _parse_state_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        logger.warning("Ignoring invalid %s state value", LAST_COMPLETED_WATCH_KEY)
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _group_listings_by_district(listings: list[Listing]) -> list[Listing]:
    groups: dict[str, list[Listing]] = {}
    for listing in listings:
        groups.setdefault(_district_label(listing), []).append(listing)
    return [listing for group in groups.values() for listing in group]


def _format_district_counts(listings: list[Listing]) -> str:
    counts: dict[str, int] = {}
    for listing in listings:
        label = _district_label(listing)
        counts[label] = counts.get(label, 0) + 1
    return "、".join(f"{district}{count}間" for district, count in counts.items()) or "0間"


def _district_label(listing: Listing) -> str:
    return listing.district or listing.city or "未知區"


def process_sources(settings: Settings, conn) -> list[ProcessedListing]:  # noqa: ANN001
    processed: list[ProcessedListing] = []
    seen_fingerprints: set[str] = set()
    for listing in fetch_all(settings):
        try:
            listing = classify_listing(listing, settings)
            listing = enrich_ntu_distance(listing, settings)
            result = listing_matches(listing, settings)
            if not result.ok:
                listing.status = "filtered_out"
                listing.raw_json["filter_reasons"] = result.reasons
                upsert_listing(conn, listing)
                logger.info("Filtered out %s: %s", listing.url, "; ".join(result.reasons))
                continue
            listing.status = "active"
            fingerprint = fingerprint_listing(listing)
            if fingerprint:
                listing.raw_json["dedupe_fingerprint"] = fingerprint
                if fingerprint in seen_fingerprints:
                    listing.raw_json["possible_duplicate"] = True
                    listing.raw_json["notification_suppressed"] = True
                    upsert_listing(conn, listing)
                    logger.info("Possible duplicate skipped for notification: %s", listing.url)
                    continue
                seen_fingerprints.add(fingerprint)
            upsert = upsert_listing(conn, listing)
            processed.append(ProcessedListing(listing=listing, upsert=upsert))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to process listing %s: %s", listing.url, exc)
    conn.commit()
    return processed


def fetch_all(settings: Settings) -> Iterable[Listing]:
    sources = [
        SourcePTT(settings),
        Source591(settings),
        SourceRakuya(settings),
        SourceYungching(settings),
        SourceHouseprice(settings),
        SourceSinyi(settings),
    ]
    seen: set[tuple[str, str]] = set()
    for source in sources:
        try:
            result = source.fetch()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Source %s failed: %s", source.name, exc)
            continue
        for error in result.errors:
            logger.info("%s: %s", source.name, error)
        logger.info("%s returned %s listings", source.name, len(result.listings))
        for listing in result.listings:
            key = (listing.source, listing.listing_id)
            if key in seen:
                continue
            seen.add(key)
            yield listing


def test_591_command(settings: Settings, urls: list[str] | None = None) -> None:
    if urls:
        settings.source_591_search_urls = urls
    settings.source_591_enabled = True
    source = Source591(settings)
    result = source.fetch()
    print(f"591 listings: {len(result.listings)}")
    for error in result.errors:
        print(f"591 error: {error}")
    for listing in result.listings[:20]:
        cost = listing.total_monthly_cost or listing.rent or 0
        location = f"{listing.city or ''}{listing.district or ''}"
        print(f"{cost:>6} | {listing.area_ping or '?'}坪 | {location} | {listing.title} | {listing.url}")


def test_source_command(label: str, result: SourceResult) -> None:
    print(f"{label} listings: {len(result.listings)}")
    for error in result.errors:
        print(f"{label} error: {error}")
    for listing in result.listings[:3]:
        cost = listing.total_monthly_cost or listing.rent or 0
        location = f"{listing.city or ''}{listing.district or ''}"
        duplicate = " duplicate?" if listing.raw_json.get("possible_duplicate") else ""
        print(f"{cost:>6} | {listing.area_ping or '?'}坪 | {location} | {listing.room_type or '?'} | {listing.title}{duplicate} | {listing.url}")


def classify_listing(listing: Listing, settings: Settings) -> Listing:
    classification: Classification
    if settings.enable_openai_classifier and settings.openai_api_key:
        from rent_bot.optional.openai_classifier import classify_with_openai

        classification = classify_with_openai(listing, settings.openai_api_key, settings.openai_model)
    else:
        classification = keyword_classify(listing)
    return apply_classification(listing, classification)


def list_command(settings: Settings, limit: int = 20) -> None:
    with connect(settings.database_path) as conn:
        listings = list_recent(conn, limit=limit)
    for listing in listings:
        cost = listing.total_monthly_cost or listing.rent or 0
        location = f"{listing.city or ''}{listing.district or ''}"
        print(
            f"{listing.last_seen_at} | {listing.status:12} | {cost:>6} | "
            f"{listing.area_ping or '?'}坪 | {location} | {listing.title} | {listing.url}"
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
