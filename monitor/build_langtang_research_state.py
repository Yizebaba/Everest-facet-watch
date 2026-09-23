"""Build OCHA-style research state from offline Langtang adjusted z scores.

This intentionally persists a research-only table. It never writes web JSON,
changes an HTML page, enables polling, or sends notifications.
"""
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


DATABASE = Path("/app/data/facet-watch.sqlite3")
REGION = "langtang"
SCORE_VERSION = "ocha-live-build-v1-doy12-pool10-quality80"
STATE_VERSION = "ocha-live-build-v1-run3-season152-237-live90"
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
        CREATE TABLE IF NOT EXISTS facet_research_state (
            region TEXT NOT NULL,
            facet_id TEXT NOT NULL,
            as_of_acquired_at TEXT NOT NULL,
            live_window_start TEXT NOT NULL,
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
            SELECT facet_id, acquired_at, adjusted_z
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
        season = scores[(scores["doy"] >= SEASON_START) & (scores["doy"] <= SEASON_END)]

        historical_by_facet = {}
        for (facet_id, year), group in season.groupby(["facet_id", "year"], sort=False):
            statistic = worst_run(group.sort_values("acquired_at")["adjusted_z"])
            if statistic is not None and year <= HISTORY_END_YEAR:
                historical_by_facet.setdefault(facet_id, []).append(statistic)
        # OCHA live_build.py ranks each current live statistic against the
        # region-wide 2020-2025 facet-season null, not only its own history.
        historical_null = [
            value for values in historical_by_facet.values() for value in values
        ]
        if not historical_null:
            raise RuntimeError("No historical 2020-2025 facet-season statistics available.")

        live = {}
        for facet_id, group in scores[scores["acquired_at"] >= live_start].groupby("facet_id", sort=False):
            statistic = worst_run(group.sort_values("acquired_at")["adjusted_z"])
            if statistic is not None:
                live[facet_id] = statistic

        facets = sorted(set(scores["facet_id"]))
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = []
        for facet_id in facets:
            live_stat = live.get(facet_id)
            if live_stat is None:
                percentile, tier = None, "nodata"
            else:
                percentile = round(100 * sum(value <= live_stat for value in historical_null) / len(historical_null), 1)
                tier = tier_for(percentile)
            rows.append(
                (
                    REGION,
                    facet_id,
                    as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    live_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    live_stat,
                    len(historical_null),
                    percentile,
                    tier,
                    STATE_VERSION,
                    timestamp,
                )
            )
        connection.execute(
            "DELETE FROM facet_research_state WHERE region = ? AND state_version = ?",
            (REGION, STATE_VERSION),
        )
        connection.executemany(
            """
            INSERT INTO facet_research_state
            (region, facet_id, as_of_acquired_at, live_window_start, live_run3_adjusted_z,
             historical_season_count, historical_percentile, research_tier, state_version, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        counts = connection.execute(
            """
            SELECT research_tier, COUNT(*) FROM facet_research_state
            WHERE region = ? AND state_version = ? GROUP BY research_tier ORDER BY research_tier
            """,
            (REGION, STATE_VERSION),
        ).fetchall()
    print(
        {
            "region": REGION,
            "as_of_acquired_at": as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "live_window_start": live_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "facets": len(rows),
            "tiers": dict(counts),
            "status": "research_state_only_no_web_publication_or_notification",
        }
    )


if __name__ == "__main__":
    main()
