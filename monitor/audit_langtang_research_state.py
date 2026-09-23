"""Produce an unpublished provenance audit for each Langtang 3-observation run."""
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


DATABASE = Path("/app/data/facet-watch.sqlite3")
REGION = "langtang"
SCORE_VERSION = "ocha-live-build-v1-doy12-pool10-quality80"
STATE_VERSION = "ocha-live-build-v1-run3-season152-237-live90"
AUDIT_VERSION = "langtang-research-audit-v1"
RUN_LENGTH = 3
EXTREME_COMPONENT_Z = 8.0


def initialize(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS facet_research_audit (
            region TEXT NOT NULL,
            facet_id TEXT NOT NULL,
            run_position INTEGER NOT NULL,
            scene_id TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            orbit INTEGER,
            platform TEXT,
            value_db REAL NOT NULL,
            valid_pixels INTEGER NOT NULL,
            z_score REAL NOT NULL,
            adjusted_z REAL NOT NULL,
            run_stat REAL NOT NULL,
            run_crosses_orbits INTEGER NOT NULL,
            run_crosses_platforms INTEGER NOT NULL,
            component_extreme INTEGER NOT NULL,
            audit_version TEXT NOT NULL,
            audited_at TEXT NOT NULL,
            PRIMARY KEY (region, facet_id, run_position, audit_version)
        )
        """
    )


def main() -> None:
    with sqlite3.connect(DATABASE) as connection:
        initialize(connection)
        state = pd.read_sql_query(
            """
            SELECT facet_id, live_run3_adjusted_z
            FROM facet_research_state
            WHERE region = ? AND state_version = ? AND live_run3_adjusted_z IS NOT NULL
            """,
            connection,
            params=(REGION, STATE_VERSION),
        )
        scores = pd.read_sql_query(
            """
            SELECT facet_id, scene_id, acquired_at, orbit, value_db, valid_pixels,
                   z_score, adjusted_z
            FROM facet_observation_scores
            WHERE region = ? AND scoring_version = ? AND adjusted_z IS NOT NULL
              AND acquired_at >= (
                  SELECT MIN(live_window_start) FROM facet_research_state
                  WHERE region = ? AND state_version = ?
              )
            ORDER BY facet_id, acquired_at
            """,
            connection,
            params=(REGION, SCORE_VERSION, REGION, STATE_VERSION),
            parse_dates=["acquired_at"],
        )
        platforms = pd.read_sql_query(
            """
            SELECT facet_id, scene_id, platform
            FROM facet_observations
            WHERE region = ? AND polarization = 'VV'
            """,
            connection,
            params=(REGION,),
        )
        scores = scores.merge(platforms, on=["facet_id", "scene_id"], how="left")
        target = state.set_index("facet_id")["live_run3_adjusted_z"].to_dict()
        records = []
        for facet_id, group in scores.groupby("facet_id", sort=False):
            if facet_id not in target or len(group) < RUN_LENGTH:
                continue
            group = group.reset_index(drop=True)
            candidates = [
                group.iloc[index : index + RUN_LENGTH]
                for index in range(len(group) - RUN_LENGTH + 1)
                if abs(group.iloc[index : index + RUN_LENGTH]["adjusted_z"].max() - target[facet_id]) < 1e-9
            ]
            if not candidates:
                raise RuntimeError(f"No matching 3-run provenance for {facet_id}")
            run = candidates[0]
            run_stat = float(run["adjusted_z"].max())
            crosses_orbits = int(run["orbit"].nunique() > 1)
            crosses_platforms = int(run["platform"].nunique() > 1)
            timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            for position, row in enumerate(run.itertuples(index=False), start=1):
                records.append(
                    (
                        REGION,
                        facet_id,
                        position,
                        row.scene_id,
                        row.acquired_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        int(row.orbit),
                        row.platform,
                        float(row.value_db),
                        int(row.valid_pixels),
                        float(row.z_score),
                        float(row.adjusted_z),
                        run_stat,
                        crosses_orbits,
                        crosses_platforms,
                        int(abs(row.z_score) >= EXTREME_COMPONENT_Z),
                        AUDIT_VERSION,
                        timestamp,
                    )
                )
        connection.execute(
            "DELETE FROM facet_research_audit WHERE region = ? AND audit_version = ?",
            (REGION, AUDIT_VERSION),
        )
        connection.executemany(
            """
            INSERT INTO facet_research_audit
            (region, facet_id, run_position, scene_id, acquired_at, orbit, platform, value_db,
             valid_pixels, z_score, adjusted_z, run_stat, run_crosses_orbits, run_crosses_platforms,
             component_extreme, audit_version, audited_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )
        summary = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT facet_id), SUM(run_crosses_orbits),
                   SUM(run_crosses_platforms), SUM(component_extreme)
            FROM facet_research_audit
            WHERE region = ? AND audit_version = ?
            """,
            (REGION, AUDIT_VERSION),
        ).fetchone()
    print(
        json.dumps(
            {
                "audit_rows": summary[0],
                "audited_facets": summary[1],
                "rows_in_cross_orbit_runs": summary[2],
                "rows_in_cross_platform_runs": summary[3],
                "extreme_component_rows_abs_z_gte_8": summary[4],
                "status": "audit_only_not_published_or_notified",
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
