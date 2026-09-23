"""Read-only platform and orbit coverage diagnosis for Langtang history."""
import json
import sqlite3
from pathlib import Path


DATABASE = Path("/app/data/facet-watch.sqlite3")


def main() -> None:
    with sqlite3.connect(DATABASE) as connection:
        coverage = connection.execute(
            """
            SELECT platform, orbit, MIN(substr(acquired_at, 1, 10)),
                   MAX(substr(acquired_at, 1, 10)), COUNT(DISTINCT scene_id),
                   COUNT(DISTINCT facet_id), COUNT(*)
            FROM facet_observations
            WHERE region = 'langtang' AND polarization = 'VV'
            GROUP BY platform, orbit
            ORDER BY platform, orbit
            """
        ).fetchall()
        recent = connection.execute(
            """
            SELECT platform, orbit, COUNT(DISTINCT scene_id), COUNT(DISTINCT facet_id)
            FROM facet_observations
            WHERE region = 'langtang' AND polarization = 'VV'
              AND acquired_at >= '2026-06-19T00:18:45Z'
            GROUP BY platform, orbit
            ORDER BY platform, orbit
            """
        ).fetchall()
    print(
        json.dumps(
            {
                "all_history": [
                    {
                        "platform": row[0], "orbit": row[1], "first": row[2], "last": row[3],
                        "scenes": row[4], "facets": row[5], "observations": row[6],
                    }
                    for row in coverage
                ],
                "recent_90_days": [
                    {"platform": row[0], "orbit": row[1], "scenes": row[2], "facets": row[3]}
                    for row in recent
                ],
                "status": "diagnostic_only_no_state_or_page_change",
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
