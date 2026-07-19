from __future__ import annotations

import unittest
from urllib.robotparser import RobotFileParser

from rent_bot.config import Settings
from rent_bot.sources.base import BaseSource, SourceResult


class DummySource(BaseSource):
    name = "dummy"

    def fetch(self) -> SourceResult:
        return SourceResult(listings=[], errors=[])


class BaseSourceTest(unittest.TestCase):
    def test_can_fetch_respects_robots_disallow(self) -> None:
        parser = RobotFileParser()
        parser.parse(["User-agent: *", "Disallow: /private"])
        source = DummySource(Settings(request_min_delay_seconds=0, request_max_delay_seconds=0))
        source._read_robots_txt = lambda _url: parser  # type: ignore[method-assign]

        self.assertFalse(source.can_fetch("https://example.com/private/page.html"))
        self.assertTrue(source.can_fetch("https://example.com/public/page.html"))

    def test_can_fetch_fails_closed_when_robots_unavailable(self) -> None:
        source = DummySource(Settings(request_min_delay_seconds=0, request_max_delay_seconds=0))
        source._read_robots_txt = lambda _url: False  # type: ignore[method-assign]

        self.assertFalse(source.can_fetch("https://example.com/anything"))

    def test_settings_reject_invalid_delay_range(self) -> None:
        with self.assertRaises(ValueError):
            Settings(request_min_delay_seconds=5, request_max_delay_seconds=2)


if __name__ == "__main__":
    unittest.main()
