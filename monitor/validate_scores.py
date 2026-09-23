"""Read-only checks for derived Langtang anomaly statistics."""
import json
import sqlite3
from pathlib import Path


DATABASE = Path("/app/data/facet-watch.sqlite3")
REGION = "langtang"
VERSION = "ocha-live-build-v1-doy12-pool10-quality80"


def main() -> None:
    with sqlite3.connect(DATABASE) as connection:
        totals = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT facet_id), COUNT(DISTINCT scene_id),
                   SUM(quality_pass), SUM(z_score IS NOT NULL), SUM(adjusted_z IS NOT NULL),
                   MIN(adjusted_z), MAX(adjusted_z)
            FROM facet_observation_scores
            WHERE region = ? AND scoring_version = ?
            """,
            (REGION, VERSION),
        ).fetchone()
        duplicates = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT region, facet_id, scene_id, polarization, COUNT(*) AS n
                FROM facet_observation_scores
                GROUP BY region, facet_id, scene_id, polarization
                HAVING n > 1
            )
            """
        ).fetchone()[0]
        coverage = connection.execute(
            """
            SELECT MIN(n), MAX(n), AVG(n) FROM (
                SELECT facet_id, COUNT(*) AS n
                FROM facet_observation_scores
                WHERE region = ? AND scoring_version = ? AND adjusted_z IS NOT NULL
                GROUP BY facet_id
            )
            """,
            (REGION, VERSION),
        ).fetchone()
    print(
        json.dumps(
            {
                "rows": totals[0],
                "facets": totals[1],
                "scenes": totals[2],
                "quality_pass_rows": totals[3],
                "z_score_rows": totals[4],
                "adjusted_z_rows": totals[5],
                "adjusted_z_min": totals[6],
                "adjusted_z_max": totals[7],
                "duplicate_primary_keys": duplicates,
                "per_facet_adjusted_score_min": coverage[0],
                "per_facet_adjusted_score_max": coverage[1],
                "per_facet_adjusted_score_average": coverage[2],
                "status": "validated_offline_scores_no_tier_or_page_update",
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
