from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import requests
from requests import RequestException

from rent_bot.config import Settings
from rent_bot.models import Listing

logger = logging.getLogger(__name__)


class SourceErrorCode(StrEnum):
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    ROBOTS_UNAVAILABLE = "ROBOTS_UNAVAILABLE"
    SSL_ERROR = "SSL_ERROR"
    HTTP_FORBIDDEN = "HTTP_FORBIDDEN"
    HTTP_RATE_LIMITED = "HTTP_RATE_LIMITED"
    CAPTCHA_DETECTED = "CAPTCHA_DETECTED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    PARSE_ERROR = "PARSE_ERROR"
    EMPTY_RESULT = "EMPTY_RESULT"


@dataclass
class SourceError:
    code: SourceErrorCode
    message: str
    url: str | None = None

    def __str__(self) -> str:
        location = f" url={self.url}" if self.url else ""
        return f"{self.code.value}: {self.message}{location}"


class SourceBlockedError(Exception):
    def __init__(self, code: SourceErrorCode, message: str, url: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.url = url


@dataclass
class SourceResult:
    listings: list[Listing]
    errors: list[SourceError | str]


class BaseSource(ABC):
    name: str

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
        }
        )
        self._robots_cache: dict[str, RobotFileParser | bool | None] = {}

    @abstractmethod
    def fetch(self) -> SourceResult:
        raise NotImplementedError

    def polite_get(
        self,
        url: str,
        *,
        referer: str | None = None,
        raise_on_block: bool = False,
    ) -> requests.Response | None:
        robots_block = self.robots_block_reason(url)
        if robots_block is not None:
            if robots_block == SourceErrorCode.ROBOTS_UNAVAILABLE:
                message = "robots.txt unavailable; failing closed"
            else:
                message = "robots.txt disallows fetching"
            logger.warning("%s %s %s", self.name, message, url)
            if raise_on_block:
                raise SourceBlockedError(robots_block, message, url)
            return None
        self._sleep_before_request()
        headers = {"Referer": referer} if referer else None
        response = self.session.get(url, headers=headers, timeout=self.settings.http_timeout_seconds)
        if response.status_code in {401, 403, 429}:
            logger.warning("%s returned %s for %s; not retrying aggressively", self.name, response.status_code, url)
            if raise_on_block:
                if response.status_code == 429:
                    code = SourceErrorCode.HTTP_RATE_LIMITED
                elif response.status_code == 401:
                    code = SourceErrorCode.LOGIN_REQUIRED
                else:
                    code = SourceErrorCode.HTTP_FORBIDDEN
                raise SourceBlockedError(code, f"HTTP {response.status_code}", url)
            return None
        access_wall = detect_access_wall(response.text)
        if access_wall is not None:
            logger.warning("%s access wall detected for %s: %s", self.name, url, access_wall.value)
            if raise_on_block:
                raise SourceBlockedError(access_wall, access_wall.value, url)
            return None
        response.raise_for_status()
        return response

    def can_fetch(self, url: str) -> bool:
        return self.robots_block_reason(url) is None

    def robots_block_reason(self, url: str) -> SourceErrorCode | None:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots_cache:
            robot_url = f"{base}/robots.txt"
            self._robots_cache[base] = self._read_robots_txt(robot_url)
        parser = self._robots_cache[base]
        if parser is False:
            return SourceErrorCode.ROBOTS_UNAVAILABLE
        if parser is None:
            return None
        if not parser.can_fetch(self.settings.user_agent, url):
            return SourceErrorCode.ROBOTS_DISALLOWED
        return None

    def _read_robots_txt(self, robot_url: str) -> RobotFileParser | bool | None:
        self._sleep_before_request()
        try:
            response = self.session.get(robot_url, timeout=self.settings.http_timeout_seconds)
        except RequestException as exc:
            logger.warning("%s could not read robots.txt %s: %s", self.name, robot_url, exc)
            return False
        if response.status_code == 404:
            return None
        if response.status_code in {401, 403, 429}:
            logger.warning("%s robots.txt blocked with HTTP %s: %s", self.name, response.status_code, robot_url)
            return False
        try:
            response.raise_for_status()
        except RequestException as exc:
            logger.warning("%s could not read robots.txt %s: %s", self.name, robot_url, exc)
            return False

        parser = RobotFileParser()
        parser.set_url(robot_url)
        parser.parse(response.text.splitlines())
        return parser

    def _sleep_before_request(self) -> None:
        delay = random.uniform(
            self.settings.request_min_delay_seconds,
            self.settings.request_max_delay_seconds,
        )
        if delay > 0:
            time.sleep(delay)


def detect_access_wall(html: str) -> SourceErrorCode | None:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
    captcha_markers = (
        "captcha",
        "recaptcha",
        "驗證碼",
        "安全驗證",
    )
    login_markers = (
        "請先登入",
        "登入後繼續",
        "login required",
    )
    rate_markers = (
        "too many requests",
        "rate limit",
        "請求過於頻繁",
    )
    if any(marker in text for marker in captcha_markers):
        return SourceErrorCode.CAPTCHA_DETECTED
    if any(marker in text for marker in login_markers):
        return SourceErrorCode.LOGIN_REQUIRED
    if any(marker in text for marker in rate_markers):
        return SourceErrorCode.HTTP_RATE_LIMITED
    if "access denied" in text or "forbidden" in text:
        return SourceErrorCode.HTTP_FORBIDDEN
    return None
