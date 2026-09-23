"""Read-only validation for the unpublished Langtang research state."""
import json
import sqlite3
from pathlib import Path


DATABASE = Path("/app/data/facet-watch.sqlite3")
REGION = "langtang"
VERSION = "ocha-live-build-v1-run3-season152-237-live90"


def main() -> None:
    with sqlite3.connect(DATABASE) as connection:
        summary = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT facet_id), MIN(as_of_acquired_at), MAX(as_of_acquired_at),
                   MIN(live_window_start), MAX(live_window_start), MIN(historical_season_count),
                   MAX(historical_season_count), MIN(historical_percentile), MAX(historical_percentile)
            FROM facet_research_state
            WHERE region = ? AND state_version = ?
            """,
            (REGION, VERSION),
        ).fetchone()
        tiers = connection.execute(
            """
            SELECT research_tier, COUNT(*) FROM facet_research_state
            WHERE region = ? AND state_version = ? GROUP BY research_tier ORDER BY research_tier
            """,
            (REGION, VERSION),
        ).fetchall()
        duplicates = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT region, facet_id, state_version, COUNT(*) AS n
                FROM facet_research_state
                GROUP BY region, facet_id, state_version
                HAVING n > 1
            )
            """
        ).fetchone()[0]
    print(
        json.dumps(
            {
                "rows": summary[0],
                "facets": summary[1],
                "as_of_min": summary[2],
                "as_of_max": summary[3],
                "live_window_start_min": summary[4],
                "live_window_start_max": summary[5],
                "historical_null_size_min": summary[6],
                "historical_null_size_max": summary[7],
                "percentile_min": summary[8],
                "percentile_max": summary[9],
                "tiers": dict(tiers),
                "duplicate_primary_keys": duplicates,
                "status": "validated_research_state_not_published",
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
