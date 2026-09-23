"""Build a comparable-only Langtang research state.

Unlike v1, every 3-observation run is constrained to one facet, relative orbit,
and Sentinel-1 platform. The historical null is built from the same comparable
series. This is database-only and remains unpublished.
"""
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


DATABASE = Path("/app/data/facet-watch.sqlite3")
REGION = "langtang"
SCORE_VERSION = "ocha-live-build-v1-doy12-pool10-quality80"
STATE_VERSION = "ocha-live-build-v2-run3-same-orbit-platform-season152-237-live90"
RUN_LENGTH = 3
SEASON_START, SEASON_END = 152, 237
HISTORY_END_YEAR = 2025
LIVE_DAYS = 90
TIERS = ((1, "critical"), (5, "elevated"), (15, "watch"), (100, "quiet"))


def worst_run(group: pd.DataFrame):
    if len(group) < RUN_LENGTH:
        return None
    group = group.sort_values("acquired_at").reset_index(drop=True)
    candidates = [group.iloc[index : index + RUN_LENGTH] for index in range(len(group) - RUN_LENGTH + 1)]
    return min(candidates, key=lambda run: run["adjusted_z"].max())


def tier_for(percentile: float) -> str:
    return next(name for limit, name in TIERS if percentile < limit or limit == 100)


def initialize(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS facet_research_state_v2 (
            region TEXT NOT NULL,
            facet_id TEXT NOT NULL,
            as_of_acquired_at TEXT NOT NULL,
            live_window_start TEXT NOT NULL,
            selected_orbit INTEGER,
            selected_platform TEXT,
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
            SELECT score.facet_id, score.scene_id, score.acquired_at, score.orbit,
                   observation.platform, score.adjusted_z
            FROM facet_observation_scores AS score
            JOIN facet_observations AS observation
              ON observation.region = score.region
             AND observation.facet_id = score.facet_id
             AND observation.scene_id = score.scene_id
             AND observation.polarization = score.polarization
            WHERE score.region = ? AND score.scoring_version = ? AND score.adjusted_z IS NOT NULL
            """,
            connection,
            params=(REGION, SCORE_VERSION),
            parse_dates=["acquired_at"],
        )
        if scores.empty:
            raise RuntimeError("No adjusted scores available.")
        as_of = scores["acquired_at"].max()
        live_start = as_of - pd.Timedelta(days=LIVE_DAYS)
        scores["year"] = scores["acquired_at"].dt.year
        scores["doy"] = scores["acquired_at"].dt.dayofyear
        grouped = ["facet_id", "orbit", "platform"]
        season = scores[(scores["doy"] >= SEASON_START) & (scores["doy"] <= SEASON_END)]
        historical = {}
        for key, group in season.groupby(grouped, sort=False):
            for year, annual in group.groupby("year", sort=False):
                run = worst_run(annual)
                if run is not None and year <= HISTORY_END_YEAR:
                    historical.setdefault(key, []).append(float(run["adjusted_z"].max()))

        live_candidates = {}
        for key, group in scores[scores["acquired_at"] >= live_start].groupby(grouped, sort=False):
            run = worst_run(group)
            if run is not None:
                live_candidates[key] = (float(run["adjusted_z"].max()), run)

        rows = []
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for facet_id in sorted(scores["facet_id"].unique()):
            options = []
            for key, (live_stat, _) in live_candidates.items():
                if key[0] != facet_id or not historical.get(key):
                    continue
                null = historical[key]
                percentile = 100 * sum(value <= live_stat for value in null) / len(null)
                options.append((percentile, live_stat, key, len(null)))
            if not options:
                selected_orbit = selected_platform = live_stat = percentile = None
                history_count, tier = 0, "nodata"
            else:
                percentile, live_stat, key, history_count = min(options, key=lambda item: (item[0], item[1]))
                _, selected_orbit, selected_platform = key
                percentile = round(percentile, 1)
                tier = tier_for(percentile)
            rows.append(
                (
                    REGION,
                    facet_id,
                    as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    live_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    selected_orbit,
                    selected_platform,
                    live_stat,
                    history_count,
                    percentile,
                    tier,
                    STATE_VERSION,
                    timestamp,
                )
            )
        connection.execute(
            "DELETE FROM facet_research_state_v2 WHERE region = ? AND state_version = ?",
            (REGION, STATE_VERSION),
        )
        connection.executemany(
            """
            INSERT INTO facet_research_state_v2
            (region, facet_id, as_of_acquired_at, live_window_start, selected_orbit, selected_platform,
             live_run3_adjusted_z, historical_season_count, historical_percentile, research_tier,
             state_version, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        counts = connection.execute(
            """
            SELECT research_tier, COUNT(*) FROM facet_research_state_v2
            WHERE region = ? AND state_version = ? GROUP BY research_tier ORDER BY research_tier
            """,
            (REGION, STATE_VERSION),
        ).fetchall()
    print({"facets": len(rows), "tiers": dict(counts), "status": "v2_comparable_only_not_published"})


if __name__ == "__main__":
    main()
