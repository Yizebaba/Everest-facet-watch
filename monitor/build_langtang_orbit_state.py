"""Build an orbit-separated Langtang research snapshot.

Each historical and recent three-observation run stays within one facet and
one relative orbit. The selected map value is the most anomalous valid orbit
candidate for that facet. This reproduces the published project's orbit
separation, but remains an unpublished research product because its input
scores have not yet received a Sentinel-1 platform radiometry correction.
"""
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


DATABASE = Path("/app/data/facet-watch.sqlite3")
REGION = "langtang"
SCORE_VERSION = "ocha-live-build-v1-doy12-pool10-quality80"
STATE_VERSION = "ocha-live-orbit-separated-v3-research"
RUN_LENGTH = 3
SEASON_START, SEASON_END = 152, 237
HISTORY_END_YEAR = 2025
LIVE_DAYS = 90
TIERS = ((1, "critical"), (5, "elevated"), (15, "watch"), (100, "quiet"))


def worst_run(values: pd.Series) -> float | None:
    if len(values) < RUN_LENGTH:
        return None
    return float(min(values.iloc[index : index + RUN_LENGTH].max() for index in range(len(values) - RUN_LENGTH + 1)))


def tier_for(percentile: float) -> str:
    return next(name for limit, name in TIERS if percentile < limit or limit == 100)


def initialize(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS facet_research_state_orbit_v3 (
            region TEXT NOT NULL,
            facet_id TEXT NOT NULL,
            as_of_acquired_at TEXT NOT NULL,
            live_window_start TEXT NOT NULL,
            selected_orbit INTEGER,
            live_run3_adjusted_z REAL,
            historical_season_count INTEGER NOT NULL,
            historical_percentile REAL,
            research_tier TEXT NOT NULL,
            state_version TEXT NOT NULL,
            built_at TEXT NOT NULL,
            PRIMARY KEY (region, facet_id, state_version)
        )
        """
    )


def main() -> None:
    with sqlite3.connect(DATABASE) as connection:
        initialize(connection)
        scores = pd.read_sql_query(
            """
            SELECT facet_id, acquired_at, orbit, adjusted_z
            FROM facet_observation_scores
            WHERE region = ? AND scoring_version = ? AND adjusted_z IS NOT NULL
            """,
            connection,
            params=(REGION, SCORE_VERSION),
            parse_dates=["acquired_at"],
        )
        if scores.empty:
            raise RuntimeError("No adjusted scores available. Run score_langtang.py first.")
        as_of = scores["acquired_at"].max()
        live_start = as_of - pd.Timedelta(days=LIVE_DAYS)
        scores["year"] = scores["acquired_at"].dt.year
        scores["doy"] = scores["acquired_at"].dt.dayofyear
        grouped = ["facet_id", "orbit"]
        season = scores[(scores["doy"] >= SEASON_START) & (scores["doy"] <= SEASON_END)]

        # The published offline snapshot ranks candidates against all valid
        # facet-season statistics. Runs themselves are never cross-orbit.
        historical = {}
        for key, group in season.groupby(grouped, sort=False):
            for year, annual in group.groupby("year", sort=False):
                statistic = worst_run(annual.sort_values("acquired_at")["adjusted_z"])
                if statistic is not None and year <= HISTORY_END_YEAR:
                    historical.setdefault(key, {})[int(year)] = statistic
        historical_null = [value for values in historical.values() for value in values.values()]
        if not historical_null:
            raise RuntimeError("No historical 2020-2025 orbit-separated statistics available.")

        live = {}
        for key, group in scores[scores["acquired_at"] >= live_start].groupby(grouped, sort=False):
            statistic = worst_run(group.sort_values("acquired_at")["adjusted_z"])
            if statistic is not None:
                live[key] = statistic

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = []
        for facet_id in sorted(scores["facet_id"].unique()):
            options = []
            for (candidate_facet, orbit), live_stat in live.items():
                if candidate_facet != facet_id:
                    continue
                history = historical.get((candidate_facet, orbit), {})
                if not history:
                    continue
                percentile = round(100 * sum(value <= live_stat for value in historical_null) / len(historical_null), 1)
                options.append((percentile, live_stat, orbit, len(history)))
            if options:
                percentile, live_stat, orbit, history_count = min(options, key=lambda item: (item[0], item[1], item[2]))
                tier = tier_for(percentile)
            else:
                percentile = live_stat = orbit = None
                history_count, tier = 0, "nodata"
            rows.append((REGION, facet_id, as_of.strftime("%Y-%m-%dT%H:%M:%SZ"), live_start.strftime("%Y-%m-%dT%H:%M:%SZ"), orbit, live_stat, history_count, percentile, tier, STATE_VERSION, timestamp))

        connection.execute("DELETE FROM facet_research_state_orbit_v3 WHERE region = ? AND state_version = ?", (REGION, STATE_VERSION))
        connection.executemany(
            """
            INSERT INTO facet_research_state_orbit_v3
            (region, facet_id, as_of_acquired_at, live_window_start, selected_orbit,
             live_run3_adjusted_z, historical_season_count, historical_percentile,
             research_tier, state_version, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        counts = connection.execute(
            "SELECT research_tier, COUNT(*) FROM facet_research_state_orbit_v3 WHERE region = ? AND state_version = ? GROUP BY research_tier ORDER BY research_tier",
            (REGION, STATE_VERSION),
        ).fetchall()
    print({"region": REGION, "as_of_acquired_at": as_of.strftime("%Y-%m-%dT%H:%M:%SZ"), "facets": len(rows), "tiers": dict(counts), "historical_null_size": len(historical_null), "status": "orbit_separated_research_only_not_published"})


if __name__ == "__main__":
    main()
