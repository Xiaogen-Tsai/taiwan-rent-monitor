from __future__ import annotations

from rent_bot.sources.rental_common import PublicRentalSource, PublicRentalSourceSpec


class SourceHouseprice(PublicRentalSource):
    spec = PublicRentalSourceSpec(
        name="houseprice_5168",
        enabled_attr="source_houseprice_enabled",
        search_urls_attr="source_houseprice_search_urls",
        max_pages_attr="source_houseprice_max_pages",
        max_age_days_attr="source_houseprice_max_age_days",
        detail_url_patterns=(
            r"rent\.houseprice\.tw/house/([A-Za-z0-9_-]+)",
        ),
    )
