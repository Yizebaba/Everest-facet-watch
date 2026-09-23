"""Read-only integrity checks for the isolated Langtang observation store."""
import json
import sqlite3
from pathlib import Path


DATABASE = Path("/app/data/facet-watch.sqlite3")


def main() -> None:
    with sqlite3.connect(DATABASE) as connection:
        summary = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT scene_id), COUNT(DISTINCT facet_id),
                   MIN(acquired_at), MAX(acquired_at), COUNT(DISTINCT orbit),
                   COUNT(DISTINCT platform), MIN(valid_pixels), MAX(valid_pixels),
                   SUM(CASE WHEN valid_pixels <= 0 THEN 1 ELSE 0 END)
            FROM facet_observations
            WHERE region = 'langtang' AND polarization = 'VV'
            """
        ).fetchone()
        duplicates = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT region, facet_id, scene_id, polarization, COUNT(*) AS n
                FROM facet_observations
                GROUP BY region, facet_id, scene_id, polarization
                HAVING n > 1
            )
            """
        ).fetchone()[0]
    print(
        json.dumps(
            {
                "rows": summary[0],
                "scenes": summary[1],
                "facets": summary[2],
                "first_acquired_at": summary[3],
                "last_acquired_at": summary[4],
                "relative_orbits": summary[5],
                "platforms": summary[6],
                "min_valid_pixels": summary[7],
                "max_valid_pixels": summary[8],
                "non_positive_pixel_rows": summary[9],
                "duplicate_primary_keys": duplicates,
                "status": "observations_only_no_scoring_or_page_update",
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
