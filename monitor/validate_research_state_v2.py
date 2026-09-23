"""Read-only coverage summary for v2 comparable-only state."""
import json
import sqlite3
from pathlib import Path

from build_langtang_research_state_v2 import REGION, STATE_VERSION


DATABASE = Path("/app/data/facet-watch.sqlite3")


def main() -> None:
    with sqlite3.connect(DATABASE) as connection:
        rows = connection.execute(
            """
            SELECT research_tier, COUNT(*), MIN(historical_season_count), MAX(historical_season_count)
            FROM facet_research_state_v2
            WHERE region = ? AND state_version = ?
            GROUP BY research_tier ORDER BY research_tier
            """,
            (REGION, STATE_VERSION),
        ).fetchall()
    print(json.dumps({"tiers": [{"tier": row[0], "facets": row[1], "history_min": row[2], "history_max": row[3]} for row in rows], "status": "v2_validation_only_not_published"}, ensure_ascii=True))


if __name__ == "__main__":
    main()
