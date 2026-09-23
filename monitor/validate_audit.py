"""Read-only audit summary grouped by unpublished research tier."""
import json
import sqlite3
from pathlib import Path


DATABASE = Path("/app/data/facet-watch.sqlite3")
REGION = "langtang"
STATE_VERSION = "ocha-live-build-v1-run3-season152-237-live90"
AUDIT_VERSION = "langtang-research-audit-v1"


def main() -> None:
    with sqlite3.connect(DATABASE) as connection:
        rows = connection.execute(
            """
            WITH run_flags AS (
                SELECT facet_id,
                       MAX(run_crosses_orbits) AS crosses_orbits,
                       MAX(run_crosses_platforms) AS crosses_platforms,
                       MAX(component_extreme) AS has_extreme_component
                FROM facet_research_audit
                WHERE region = ? AND audit_version = ?
                GROUP BY facet_id
            )
            SELECT state.research_tier,
                   COUNT(*) AS facets,
                   SUM(flags.crosses_orbits),
                   SUM(flags.crosses_platforms),
                   SUM(flags.has_extreme_component)
            FROM facet_research_state AS state
            JOIN run_flags AS flags ON flags.facet_id = state.facet_id
            WHERE state.region = ? AND state.state_version = ?
            GROUP BY state.research_tier
            ORDER BY state.research_tier
            """,
            (REGION, AUDIT_VERSION, REGION, STATE_VERSION),
        ).fetchall()
        duplicate_runs = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT facet_id, run_position, COUNT(*) AS n
                FROM facet_research_audit
                WHERE region = ? AND audit_version = ?
                GROUP BY facet_id, run_position
                HAVING n > 1
            )
            """,
            (REGION, AUDIT_VERSION),
        ).fetchone()[0]
    print(
        json.dumps(
            {
                "tiers": [
                    {
                        "tier": row[0],
                        "facets": row[1],
                        "cross_orbit_runs": row[2],
                        "cross_platform_runs": row[3],
                        "extreme_component_runs": row[4],
                    }
                    for row in rows
                ],
                "duplicate_facet_run_positions": duplicate_runs,
                "status": "audit_summary_only_not_published",
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
