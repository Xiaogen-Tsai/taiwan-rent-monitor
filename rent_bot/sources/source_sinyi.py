from __future__ import annotations

from rent_bot.sources.rental_common import PublicRentalSource, PublicRentalSourceSpec


class SourceSinyi(PublicRentalSource):
    spec = PublicRentalSourceSpec(
        name="sinyi",
        enabled_attr="source_sinyi_enabled",
        search_urls_attr="source_sinyi_search_urls",
        max_pages_attr="source_sinyi_max_pages",
        max_age_days_attr="source_sinyi_max_age_days",
        detail_url_patterns=(
            r"www\.sinyi\.com\.tw/rent/houseno/([A-Za-z0-9_-]+)",
            r"www\.sinyi\.com\.tw/rent/([A-Za-z0-9_-]{4,})(?:/|$)",
        ),
    )
