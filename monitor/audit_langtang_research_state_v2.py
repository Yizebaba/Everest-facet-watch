"""Confirm selected v2 runs use a single orbit and platform."""
import json
import sqlite3
from pathlib import Path

import pandas as pd

from build_langtang_research_state_v2 import RUN_LENGTH, SCORE_VERSION, STATE_VERSION


DATABASE = Path("/app/data/facet-watch.sqlite3")
REGION = "langtang"


def main() -> None:
    with sqlite3.connect(DATABASE) as connection:
        state = pd.read_sql_query(
            """
            SELECT facet_id, selected_orbit, selected_platform, live_run3_adjusted_z
            FROM facet_research_state_v2
            WHERE region = ? AND state_version = ? AND live_run3_adjusted_z IS NOT NULL
            """,
            connection,
            params=(REGION, STATE_VERSION),
        )
        scores = pd.read_sql_query(
            """
            SELECT score.facet_id, score.acquired_at, score.orbit, observation.platform, score.adjusted_z
            FROM facet_observation_scores AS score
            JOIN facet_observations AS observation
              ON observation.region = score.region AND observation.facet_id = score.facet_id
             AND observation.scene_id = score.scene_id AND observation.polarization = score.polarization
            WHERE score.region = ? AND score.scoring_version = ? AND score.adjusted_z IS NOT NULL
            """,
            connection,
            params=(REGION, SCORE_VERSION),
            parse_dates=["acquired_at"],
        )
        window_start = pd.read_sql_query(
            "SELECT MIN(live_window_start) AS value FROM facet_research_state_v2 WHERE region = ? AND state_version = ?",
            connection,
            params=(REGION, STATE_VERSION),
            parse_dates=["value"],
        ).iloc[0, 0]
    scores = scores[scores["acquired_at"] >= window_start]
    matched = cross_orbit = cross_platform = 0
    for row in state.itertuples(index=False):
        group = scores[(scores.facet_id == row.facet_id) & (scores.orbit == row.selected_orbit) & (scores.platform == row.selected_platform)].sort_values("acquired_at").reset_index(drop=True)
        runs = [group.iloc[index : index + RUN_LENGTH] for index in range(len(group) - RUN_LENGTH + 1)]
        run = next(run for run in runs if abs(run.adjusted_z.max() - row.live_run3_adjusted_z) < 1e-9)
        matched += 1
        cross_orbit += int(run.orbit.nunique() > 1)
        cross_platform += int(run.platform.nunique() > 1)
    print(json.dumps({"audited_facets": matched, "cross_orbit_runs": cross_orbit, "cross_platform_runs": cross_platform, "status": "v2_audit_only_not_published"}, ensure_ascii=True))


if __name__ == "__main__":
    main()
