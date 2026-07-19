from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import rent_bot.config as config_module
from rent_bot.config import PROJECT_ROOT, Settings, get_settings


def write_toml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


class TomlSettingsTest(unittest.TestCase):
    def test_product_defaults_use_taiwan_brand_and_keep_ntu_compatibility(self) -> None:
        settings = Settings()

        self.assertEqual(settings.user_agent, "TaiwanRentMonitor/0.1 personal-use")
        self.assertTrue(settings.enable_ntu_ranking)

    def test_toml_sections_map_to_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "rent.toml"
            write_toml(
                config_path,
                """
                schema_version = 1

                [filters]
                max_rent = 17500
                min_area_ping = 7.5
                suite_only = false
                exclude_female_only = false

                [filters.allowed_districts]
                "台北市" = ["大安區", "中正區"]
                "新北市" = ["永和區"]

                [schedule]
                interval_minutes = 90
                catch_up_grace_minutes = 240
                source_591_catch_up_max_pages = 4

                [notifications]
                max_results_per_run = 80
                watch_notify_limit = 30

                [ranking]
                enable_google_maps = true

                [classification]
                enable_openai_classifier = true
                openai_model = "test-model"

                [runtime]
                run_mode = "backfill"
                database_path = "state/test.sqlite3"

                [network]
                request_min_delay_seconds = 1.5
                request_max_delay_seconds = 3.5
                http_timeout_seconds = 45
                user_agent = "RentConfigTest/1.0"

                [sources.ptt]
                enabled = false
                max_pages = 5

                [sources.591]
                enabled = true
                search_urls = ["https://example.com/591-a", "https://example.com/591-b"]
                max_pages = 2
                page_size = 40
                max_age_days = 10

                [sources.rakuya]
                enabled = true
                search_urls = ["https://example.com/rakuya"]
                max_pages = 2
                max_age_days = 9

                [sources.yungching]
                enabled = true
                search_urls = ["https://example.com/yungching"]
                max_pages = 3
                max_age_days = 8

                [sources.houseprice]
                enabled = true
                search_urls = ["https://example.com/houseprice"]
                max_pages = 4
                max_age_days = 7

                [sources.sinyi]
                enabled = true
                search_urls = ["https://example.com/sinyi"]
                max_pages = 5
                max_age_days = 6
                """,
            )

            with patch.dict(os.environ, {"RENT_CONFIG_PATH": str(config_path)}, clear=True), patch(
                "rent_bot.config.load_dotenv"
            ):
                settings = get_settings()

        self.assertEqual(settings.max_rent, 17_500)
        self.assertEqual(settings.min_area_ping, 7.5)
        self.assertFalse(settings.suite_only)
        self.assertFalse(settings.exclude_female_only)
        self.assertEqual(settings.allowed_city_districts["台北市"], {"大安區", "中正區"})
        self.assertEqual(settings.crawl_interval_minutes, 90)
        self.assertEqual(settings.catch_up_grace_minutes, 240)
        self.assertEqual(settings.source_591_catch_up_max_pages, 4)
        self.assertEqual(settings.max_results_per_run, 80)
        self.assertEqual(settings.watch_notify_limit, 30)
        self.assertTrue(settings.enable_google_maps)
        self.assertTrue(settings.enable_openai_classifier)
        self.assertEqual(settings.openai_model, "test-model")
        self.assertEqual(settings.run_mode, "backfill")
        self.assertEqual(settings.database_path, PROJECT_ROOT / "state/test.sqlite3")
        self.assertEqual(settings.http_timeout_seconds, 45)
        self.assertEqual(settings.user_agent, "RentConfigTest/1.0")
        self.assertFalse(settings.source_ptt_enabled)
        self.assertEqual(settings.ptt_max_pages, 5)
        self.assertEqual(settings.source_591_search_urls, ["https://example.com/591-a", "https://example.com/591-b"])
        self.assertTrue(settings.source_rakuya_enabled)
        self.assertEqual(settings.source_yungching_max_pages, 3)
        self.assertEqual(settings.source_houseprice_max_age_days, 7)
        self.assertEqual(settings.source_sinyi_search_urls, ["https://example.com/sinyi"])

    def test_environment_values_override_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "rent.toml"
            write_toml(
                config_path,
                """
                [filters]
                max_rent = 18000

                [notifications]
                max_results_per_run = 100

                [classification]
                enable_openai_classifier = false

                [network]
                http_timeout_seconds = 30

                [sources.ptt]
                enabled = false
                max_pages = 4

                [sources.591]
                search_urls = ["https://example.com/from-toml"]
                """,
            )
            environment = {
                "RENT_CONFIG_PATH": str(config_path),
                "MAX_RENT": "16000",
                "MAX_RESULTS_PER_RUN": "7",
                "ENABLE_OPENAI_CLASSIFIER": "true",
                "HTTP_TIMEOUT_SECONDS": "44",
                "SOURCE_PTT_ENABLED": "true",
                "PTT_MAX_PAGES": "2",
                "SOURCE_591_SEARCH_URLS": "https://example.com/env-a,https://example.com/env-b",
            }

            with patch.dict(os.environ, environment, clear=True), patch("rent_bot.config.load_dotenv"):
                settings = get_settings()

        self.assertEqual(settings.max_rent, 16_000)
        self.assertEqual(settings.max_results_per_run, 7)
        self.assertTrue(settings.enable_openai_classifier)
        self.assertEqual(settings.http_timeout_seconds, 44)
        self.assertTrue(settings.source_ptt_enabled)
        self.assertEqual(settings.ptt_max_pages, 2)
        self.assertEqual(
            settings.source_591_search_urls,
            ["https://example.com/env-a", "https://example.com/env-b"],
        )

    def test_relative_rent_config_path_is_resolved_from_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temporary_root = Path(tmp)
            relative_path = Path("config") / "custom.toml"
            write_toml(
                temporary_root / relative_path,
                """
                [notifications]
                watch_notify_limit = 23
                """,
            )

            with patch.object(config_module, "PROJECT_ROOT", temporary_root), patch.dict(
                os.environ, {"RENT_CONFIG_PATH": str(relative_path)}, clear=True
            ), patch("rent_bot.config.load_dotenv"):
                settings = get_settings()

        self.assertEqual(settings.watch_notify_limit, 23)

    def test_invalid_catch_up_page_limit_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "invalid.toml"
            write_toml(
                config_path,
                """
                [schedule]
                source_591_catch_up_max_pages = 2

                [sources.591]
                max_pages = 4
                """,
            )

            with patch.dict(os.environ, {"RENT_CONFIG_PATH": str(config_path)}, clear=True), patch(
                "rent_bot.config.load_dotenv"
            ):
                with self.assertRaisesRegex(ValueError, "SOURCE_591_CATCH_UP_MAX_PAGES"):
                    get_settings()

    def test_existing_max_pages_without_catch_up_setting_remains_compatible(self) -> None:
        settings = Settings(source_591_max_pages=5)

        self.assertEqual(settings.source_591_catch_up_max_pages, 5)

    def test_top_level_locations_are_the_single_location_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "locations.toml"
            write_toml(
                config_path,
                """
                [locations]
                "臺中市" = ["西屯區"]
                "高雄市" = ["鼓山區"]

                [ranking]
                enable_ntu_ranking = false

                [sources.591]
                enabled = true
                room_types = ["獨立套房"]
                """,
            )

            with patch.dict(os.environ, {"RENT_CONFIG_PATH": str(config_path)}, clear=True), patch(
                "rent_bot.config.load_dotenv"
            ):
                settings = get_settings()

        self.assertEqual(settings.allowed_city_districts, {"台中市": {"西屯區"}, "高雄市": {"鼓山區"}})
        self.assertEqual(settings.source_591_room_types, ["獨立套房"])
        self.assertFalse(settings.enable_ntu_ranking)

    def test_locations_and_legacy_allowed_districts_cannot_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "duplicate-locations.toml"
            write_toml(
                config_path,
                """
                [locations]
                "台北市" = ["大安區"]

                [filters.allowed_districts]
                "台北市" = ["文山區"]
                """,
            )

            with patch.dict(os.environ, {"RENT_CONFIG_PATH": str(config_path)}, clear=True), patch(
                "rent_bot.config.load_dotenv"
            ):
                with self.assertRaisesRegex(ValueError, "Define locations once"):
                    get_settings()

    def test_unknown_location_fails_fast_with_valid_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "invalid-location.toml"
            write_toml(
                config_path,
                """
                [locations]
                "台北市" = ["西屯區"]
                """,
            )

            with patch.dict(os.environ, {"RENT_CONFIG_PATH": str(config_path)}, clear=True), patch(
                "rent_bot.config.load_dotenv"
            ):
                with self.assertRaisesRegex(ValueError, "Unknown district.*西屯區.*Valid values"):
                    get_settings()

    def test_example_config_is_valid(self) -> None:
        example_path = PROJECT_ROOT / "rent_config.example.toml"
        with patch.dict(os.environ, {"RENT_CONFIG_PATH": str(example_path)}, clear=True), patch(
            "rent_bot.config.load_dotenv"
        ):
            settings = get_settings()

        self.assertEqual(settings.crawl_interval_minutes, 60)
        self.assertEqual(settings.max_rent, 18_600)
        self.assertTrue(settings.source_591_enabled)
        self.assertGreaterEqual(settings.source_591_catch_up_max_pages, settings.source_591_max_pages)


if __name__ == "__main__":
    unittest.main()
