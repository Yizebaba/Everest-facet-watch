"""Publish a validated orbit-separated research snapshot as static JSON."""
import json
import sqlite3
from pathlib import Path

import pandas as pd

from build_langtang_orbit_state import (
    HISTORY_END_YEAR,
    REGION,
    SCORE_VERSION,
    SEASON_END,
    SEASON_START,
    STATE_VERSION,
    worst_run,
)


DATABASE = Path("/app/data/facet-watch.sqlite3")
FACETS = Path("/app/data/references/langtang_facets.geojson")
OUTPUT = Path("/app/data/langtang.json")


def main() -> None:
    if not FACETS.exists():
        raise RuntimeError("Missing fixed Langtang facets. Run initialize.py first.")
    feature_collection = json.loads(FACETS.read_text(encoding="utf-8"))
    features = feature_collection["features"]
    with sqlite3.connect(DATABASE) as connection:
        rows = connection.execute(
            """
            SELECT facet_id, as_of_acquired_at, live_window_start, selected_orbit,
                   live_run3_adjusted_z, historical_season_count,
                   historical_percentile, research_tier, built_at
            FROM facet_research_state_orbit_v3
            WHERE region = ? AND state_version = ?
            """,
            (REGION, STATE_VERSION),
        ).fetchall()
        scores = pd.read_sql_query(
            """
            SELECT facet_id, orbit, acquired_at, adjusted_z
            FROM facet_observation_scores
            WHERE region = ? AND scoring_version = ? AND adjusted_z IS NOT NULL
            """,
            connection,
            params=(REGION, SCORE_VERSION),
            parse_dates=["acquired_at"],
        )
    states = {row[0]: row for row in rows}
    if len(states) != len(features):
        raise RuntimeError(f"Expected {len(features)} orbit-separated states, found {len(states)}")
    selected_orbits = {facet_id: row[3] for facet_id, row in states.items() if row[3] is not None}
    scores["year"] = scores["acquired_at"].dt.year
    scores["doy"] = scores["acquired_at"].dt.dayofyear
    scores = scores[(scores["doy"] >= SEASON_START) & (scores["doy"] <= SEASON_END)]
    years = {}
    for (facet_id, orbit, year), group in scores.groupby(["facet_id", "orbit", "year"], sort=False):
        if selected_orbits.get(facet_id) != orbit or year > HISTORY_END_YEAR:
            continue
        statistic = worst_run(group.sort_values("acquired_at")["adjusted_z"])
        if statistic is not None:
            years.setdefault(facet_id, {})[str(year)] = round(statistic, 2)

    published = []
    tiers = {"critical": 0, "elevated": 0, "watch": 0, "quiet": 0, "nodata": 0}
    for feature in features:
        facet_id = feature["properties"]["facet_id"]
        _, as_of, live_start, orbit, live, history_count, percentile, tier, built_at = states[facet_id]
        tiers[tier] += 1
        published.append(
            {
                "id": facet_id,
                "aspect": feature["properties"].get("aspect"),
                "km2": feature["properties"].get("km2"),
                "tier": tier,
                "live": live,
                "pct": percentile,
                "selected_orbit": orbit,
                "historical_season_count": history_count,
                "years": years.get(facet_id, {}),
                "poly": feature["geometry"]["coordinates"][0],
            }
        )
    payload = {
        "schema_version": 1,
        "product": "Langtang orbit-separated Sentinel-1 research snapshot",
        "status": "research_only_not_warning",
        "generated_at": built_at,
        "as_of_acquired_at": as_of,
        "live_window_start": live_start,
        "method": {
            "orbit_handling": "Each three-observation run and its source score series are restricted to one facet and one relative orbit. The displayed facet value is selected only after independent orbit candidates are calculated.",
            "platform_handling": "Sentinel-1 platform radiometry has not been independently harmonized; this snapshot is not an operational product or warning.",
            "source_score_version": "ocha-live-build-v1-doy12-pool10-quality80",
            "state_version": STATE_VERSION,
        },
        "tiers": tiers,
        "facets": published,
    }
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    print({"output": str(OUTPUT), "facets": len(published), "tiers": tiers, "status": "published_static_research_snapshot_not_notified"})


if __name__ == "__main__":
    main()
