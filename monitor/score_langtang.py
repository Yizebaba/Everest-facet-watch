"""Build offline OCHA-style Langtang observation scores from SQLite history.

No web feed, tier, scheduler, or notification is produced here. This persists
only reproducible intermediate z and regional-adjusted z values.
"""
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


DATABASE = Path("/app/data/facet-watch.sqlite3")
REGION = "langtang"
DOY_WINDOW = 12
MIN_POOL = 10
MIN_SCENE_FACETS = 30
VERSION = "ocha-live-build-v1-doy12-pool10-quality80"


def initialize_scores(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS facet_observation_scores (
            region TEXT NOT NULL,
            facet_id TEXT NOT NULL,
            scene_id TEXT NOT NULL,
            polarization TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            orbit INTEGER,
            value_db REAL NOT NULL,
            valid_pixels INTEGER NOT NULL,
            quality_pass INTEGER NOT NULL,
            z_score REAL,
            regional_median_z REAL,
            regional_count INTEGER NOT NULL,
            adjusted_z REAL,
            scoring_version TEXT NOT NULL,
            scored_at TEXT NOT NULL,
            PRIMARY KEY (region, facet_id, scene_id, polarization)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS facet_observation_scores_region_time "
        "ON facet_observation_scores (region, acquired_at)"
    )


def leave_one_out_z(group: pd.DataFrame) -> pd.DataFrame:
    group = group.sort_values("acquired_at").copy()
    doy = group["doy"].to_numpy()
    years = group["year"].to_numpy()
    values = group["value_db"].to_numpy()
    scores = np.full(len(group), np.nan)
    for index in range(len(group)):
        difference = np.minimum(np.abs(doy - doy[index]), 365 - np.abs(doy - doy[index]))
        pool = (difference <= DOY_WINDOW) & (years != years[index])
        if pool.sum() >= MIN_POOL:
            standard_deviation = values[pool].std(ddof=1)
            if standard_deviation > 0:
                scores[index] = (values[index] - values[pool].mean()) / standard_deviation
    group["z_score"] = scores
    return group


def main() -> None:
    with sqlite3.connect(DATABASE) as connection:
        initialize_scores(connection)
        observations = pd.read_sql_query(
            """
            SELECT facet_id, scene_id, acquired_at, orbit, value_db, valid_pixels, platform
            FROM facet_observations
            WHERE region = ? AND polarization = 'VV'
            """,
            connection,
            params=(REGION,),
            parse_dates=["acquired_at"],
        )
        if observations.empty:
            raise RuntimeError("No Langtang VV observations available. Run backfill_langtang.py first.")
        observations["quality_pass"] = (
            observations["valid_pixels"]
            >= 0.8 * observations.groupby(["facet_id", "orbit"])["valid_pixels"].transform("max")
        )
        observations["year"] = observations["acquired_at"].dt.year
        observations["doy"] = observations["acquired_at"].dt.dayofyear
        quality = observations[observations["quality_pass"]].copy()
        scored = pd.concat(
            [leave_one_out_z(group) for _, group in quality.groupby(["facet_id", "orbit"], sort=False)],
            ignore_index=True,
        )
        scored["scene_date"] = scored["acquired_at"].dt.date.astype(str)
        controls = scored.dropna(subset=["z_score"]).groupby(["orbit", "scene_date"])["z_score"].agg(
            regional_median_z="median", regional_count="size"
        )
        scored = scored.join(controls, on=["orbit", "scene_date"])
        scored["regional_count"] = scored["regional_count"].fillna(0).astype(int)
        scored["adjusted_z"] = np.where(
            scored["regional_count"] >= MIN_SCENE_FACETS,
            scored["z_score"] - scored["regional_median_z"],
            np.nan,
        )

        all_rows = observations.merge(
            scored[
                [
                    "facet_id",
                    "scene_id",
                    "z_score",
                    "regional_median_z",
                    "regional_count",
                    "adjusted_z",
                ]
            ],
            on=["facet_id", "scene_id"],
            how="left",
        )
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        records = [
            (
                REGION,
                row.facet_id,
                row.scene_id,
                "VV",
                row.acquired_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                int(row.orbit),
                float(row.value_db),
                int(row.valid_pixels),
                int(row.quality_pass),
                None if pd.isna(row.z_score) else float(row.z_score),
                None if pd.isna(row.regional_median_z) else float(row.regional_median_z),
                int(0 if pd.isna(row.regional_count) else row.regional_count),
                None if pd.isna(row.adjusted_z) else float(row.adjusted_z),
                VERSION,
                timestamp,
            )
            for row in all_rows.itertuples(index=False)
        ]
        connection.execute(
            "DELETE FROM facet_observation_scores WHERE region = ? AND scoring_version = ?",
            (REGION, VERSION),
        )
        connection.executemany(
            """
            INSERT INTO facet_observation_scores
            (region, facet_id, scene_id, polarization, acquired_at, orbit, value_db, valid_pixels,
             quality_pass, z_score, regional_median_z, regional_count, adjusted_z, scoring_version, scored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )
        summary = connection.execute(
            """
            SELECT COUNT(*), SUM(quality_pass), SUM(z_score IS NOT NULL), SUM(adjusted_z IS NOT NULL),
                   MIN(adjusted_z), MAX(adjusted_z)
            FROM facet_observation_scores
            WHERE region = ? AND scoring_version = ?
            """,
            (REGION, VERSION),
        ).fetchone()
    print(
        {
            "region": REGION,
            "scoring_version": VERSION,
            "observations": summary[0],
            "quality_pass": summary[1],
            "z_scores": summary[2],
            "adjusted_z_scores": summary[3],
            "adjusted_z_min": summary[4],
            "adjusted_z_max": summary[5],
            "status": "offline_scores_only_no_tier_or_page_update",
        }
    )


if __name__ == "__main__":
    main()
