from __future__ import annotations

import logging
import re

import requests

logger = logging.getLogger(__name__)


DESTINATION = "國立台灣大學校總區, 台北市大安區羅斯福路四段1號"
COMPUTE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def estimate_minutes_to_ntu(origin: str, api_key: str, timeout_seconds: int = 20) -> int | None:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.duration,routes.staticDuration,routes.localizedValues",
    }
    payload = {
        "origin": {"address": origin},
        "destination": {"address": DESTINATION},
        "travelMode": "TRANSIT",
        "computeAlternativeRoutes": False,
        "languageCode": "zh-TW",
        "units": "METRIC",
    }
    response = requests.post(COMPUTE_ROUTES_URL, headers=headers, json=payload, timeout=timeout_seconds)
    if response.status_code >= 400:
        logger.warning("Google Routes API failed: %s %s", response.status_code, response.text[:300])
        return None
    data = response.json()
    routes = data.get("routes") or []
    if not routes:
        return None
    duration = routes[0].get("duration") or routes[0].get("staticDuration")
    seconds = _duration_to_seconds(duration)
    if seconds is None:
        return None
    return max(1, round(seconds / 60))


def _duration_to_seconds(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)s", value)
    if not match:
        return None
    return int(float(match.group(1)))
