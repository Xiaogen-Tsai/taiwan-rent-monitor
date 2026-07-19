from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from rent_bot.config import Settings
from rent_bot.models import Listing
from rent_bot.notifier_discord import (
    _listing_to_embed,
    _ntu_value,
    send_listing_embeds,
    send_listing_embeds_detailed,
    send_text_message,
)


class DiscordEmbedTest(unittest.TestCase):
    def test_listing_embed_includes_floor(self) -> None:
        listing = Listing(
            source="591",
            listing_id="floor-1",
            url="https://rent.591.com.tw/123",
            title="大安區套房",
            city="台北市",
            district="大安區",
            rent=15000,
            area_ping=7,
            room_type="獨立套房",
            floor="3F/5F",
            has_garbage_collection=True,
        )

        embed = _listing_to_embed(listing, [])
        fields = {field["name"]: field["value"] for field in embed["fields"]}

        self.assertEqual(fields["樓層"], "3F/5F")
        self.assertEqual(fields["垃圾代收"], "✅")

    def test_ntu_fallback_value_explains_basis_and_confidence(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="ntu-score",
            url="https://example.com/ntu-score",
            title="大安區套房",
            near_ntu_score=96,
            raw_json={
                "ntu_score_confidence": "high",
                "ntu_score_reason": "地址符合台大周邊路段：羅斯福路四段",
            },
        )

        value = _ntu_value(listing)

        self.assertIn("96/100", value)
        self.assertIn("高信心", value)
        self.assertIn("羅斯福路四段", value)

    def test_listing_embed_omits_ntu_field_when_ranking_is_disabled(self) -> None:
        listing = Listing(
            source="fixture",
            listing_id="general-taiwan",
            url="https://example.com/general-taiwan",
            title="西屯區套房",
            city="台中市",
            district="西屯區",
            raw_json={"ntu_scoring_enabled": False},
        )

        embed = _listing_to_embed(listing, [])
        field_names = {field["name"] for field in embed["fields"]}

        self.assertNotIn("距台大", field_names)

    def test_detailed_send_returns_exact_successful_listings(self) -> None:
        listings = [
            Listing(
                source="fixture",
                listing_id=str(index),
                url=f"https://example.com/{index}",
                title=f"套房 {index}",
            )
            for index in range(3)
        ]
        settings = Settings(discord_webhook_url="https://discord.com/api/webhooks/123/token")

        with (
            patch("rent_bot.notifier_discord._post_embeds", side_effect=[True, False, True]),
            patch("rent_bot.notifier_discord.time.sleep"),
        ):
            sent = send_listing_embeds_detailed(settings, listings)

        self.assertEqual([listing.listing_id for listing in sent], ["0", "2"])

    def test_legacy_send_api_still_returns_count(self) -> None:
        listings = [
            Listing(source="fixture", listing_id="1", url="https://example.com/1", title="套房 1"),
            Listing(source="fixture", listing_id="2", url="https://example.com/2", title="套房 2"),
        ]
        settings = Settings(discord_webhook_url="https://discord.com/api/webhooks/123/token")

        with (
            patch("rent_bot.notifier_discord._post_embeds", side_effect=[True, False]),
            patch("rent_bot.notifier_discord.time.sleep"),
        ):
            sent = send_listing_embeds(settings, listings)

        self.assertEqual(sent, 1)

    def test_request_exception_log_redacts_webhook_url(self) -> None:
        webhook = "https://discord.com/api/webhooks/123/super-secret-token"
        settings = Settings(discord_webhook_url=webhook)
        error = requests.ConnectionError(f"connection failed for {webhook}")

        with (
            patch("rent_bot.notifier_discord.requests.post", side_effect=error),
            self.assertLogs("rent_bot.notifier_discord", level="ERROR") as captured,
        ):
            ok = send_text_message(settings, "test")

        output = "\n".join(captured.output)
        self.assertFalse(ok)
        self.assertNotIn(webhook, output)
        self.assertNotIn("super-secret-token", output)
        self.assertIn("<redacted Discord webhook>", output)

    def test_error_response_log_redacts_webhook_url(self) -> None:
        webhook = "https://discord.com/api/webhooks/123/super-secret-token"
        settings = Settings(discord_webhook_url=webhook)
        response = Mock(status_code=500, text=f"upstream echoed {webhook}")

        with (
            patch("rent_bot.notifier_discord.requests.post", return_value=response),
            self.assertLogs("rent_bot.notifier_discord", level="ERROR") as captured,
        ):
            ok = send_text_message(settings, "test")

        output = "\n".join(captured.output)
        self.assertFalse(ok)
        self.assertNotIn(webhook, output)
        self.assertNotIn("super-secret-token", output)
        self.assertIn("<redacted Discord webhook>", output)


if __name__ == "__main__":
    unittest.main()
