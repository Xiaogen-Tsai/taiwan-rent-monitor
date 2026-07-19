from __future__ import annotations

from rent_bot.sources.rental_common import PublicRentalSource, PublicRentalSourceSpec


class SourceYungching(PublicRentalSource):
    spec = PublicRentalSourceSpec(
        name="yungching",
        enabled_attr="source_yungching_enabled",
        search_urls_attr="source_yungching_search_urls",
        max_pages_attr="source_yungching_max_pages",
        max_age_days_attr="source_yungching_max_age_days",
        detail_url_patterns=(
            r"rent\.yungching\.com\.tw/house/([A-Za-z0-9_-]+)",
            r"rent\.yungching\.com\.tw/list/.*/house/([A-Za-z0-9_-]+)",
        ),
    )
