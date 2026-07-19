from __future__ import annotations

from rent_bot.sources.rental_common import PublicRentalSource, PublicRentalSourceSpec


class SourceRakuya(PublicRentalSource):
    spec = PublicRentalSourceSpec(
        name="rakuya",
        enabled_attr="source_rakuya_enabled",
        search_urls_attr="source_rakuya_search_urls",
        max_pages_attr="source_rakuya_max_pages",
        max_age_days_attr="source_rakuya_max_age_days",
        detail_url_patterns=(
            r"community\.rakuya\.com\.tw/\d+/rent/([A-Za-z0-9_-]+)",
            r"rent\.rakuya\.com\.tw/(?:rent_detail|detail|house)/([A-Za-z0-9_-]+)",
            r"rent\.rakuya\.com\.tw/.*/rent/([A-Za-z0-9_-]+)",
        ),
    )
