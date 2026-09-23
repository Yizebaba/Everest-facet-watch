"""Resumable OCHA-style historical VV extraction for fixed Langtang facets.

This is a manual backfill command. It does not score observations, update web
data, or enable the resident monitor loop.
"""
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import ee

from monitor import earth_engine_credentials, initialize_database


DATA = Path("/app/data")
FACETS = DATA / "references" / "langtang_facets.geojson"
CONFIG = Path(os.environ.get("FACET_WATCH_CONFIG", "/app/config/monitor.json"))
REGION = "langtang"


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not FACETS.exists():
        raise RuntimeError("Run initialize.py before historical backfill.")
    start, end = config["langtang_history_start"], config["langtang_history_end"]
    batch_size = int(config["facet_batch_size"])
    source = json.loads(FACETS.read_text(encoding="utf-8"))["features"]
    credentials, project = earth_engine_credentials()
    ee.Initialize(credentials=credentials, project=project)
    initialize_database(DATA / "facet-watch.sqlite3")

    all_points = [point for feature in source for point in feature["geometry"]["coordinates"][0]]
    bounds = ee.Geometry.MultiPoint(all_points).bounds()
    collection = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(bounds)
        .filterDate(start, end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.eq("orbitProperties_pass", "DESCENDING"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select("VV")
    )
    scene_count = collection.size().getInfo()
    print(f"{REGION}: {len(source)} facets, {scene_count} descending VV scenes, {start} to {end}")
    reducer = ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True)
    inserted = 0

    with sqlite3.connect(DATA / "facet-watch.sqlite3") as database:
        for offset in range(0, len(source), batch_size):
            subset = source[offset : offset + batch_size]
            facets = ee.FeatureCollection(
                [
                    ee.Feature(ee.Geometry(feature["geometry"]), {"facet_id": feature["properties"]["facet_id"]})
                    for feature in subset
                ]
            )

            def per_image(image):
                return image.reduceRegions(facets, reducer, 30).map(
                    lambda feature: ee.Feature(
                        None,
                        {
                            "scene_id": image.get("system:index"),
                            "acquired_at": image.date().format("YYYY-MM-dd'T'HH:mm:ss'Z'"),
                            "orbit": image.get("relativeOrbitNumber_start"),
                            "platform": image.get("platform_number"),
                            "facet_id": feature.get("facet_id"),
                            "vv_db": feature.get("mean"),
                            "valid_pixels": feature.get("count"),
                        },
                    )
                )

            for year in range(int(start[:4]), int(end[:4]) + 1):
                year_start = f"{year}-01-01"
                year_end = min(f"{year + 1}-01-01", end)
                if year_start >= end:
                    continue
                yearly = collection.filterDate(year_start, year_end)
                result = ee.FeatureCollection(yearly.map(per_image)).flatten().filter(
                    ee.Filter.notNull(["vv_db"])
                ).getInfo()["features"]
                rows = [
                    (
                        REGION,
                        item["properties"]["facet_id"],
                        item["properties"]["scene_id"],
                        item["properties"]["acquired_at"],
                        item["properties"].get("orbit"),
                        item["properties"].get("platform"),
                        "VV",
                        item["properties"]["vv_db"],
                        item["properties"].get("valid_pixels", 0),
                    )
                    for item in result
                ]
                before = database.total_changes
                database.executemany(
                    """
                    INSERT OR IGNORE INTO facet_observations
                    (region, facet_id, scene_id, acquired_at, orbit, platform, polarization, value_db, valid_pixels)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                added = database.total_changes - before
                database.commit()
                inserted += added
                print(
                    f"batch {offset // batch_size + 1}, {year}: fetched {len(rows)}, inserted {added}, total inserted {inserted}",
                    flush=True,
                )

        totals = database.execute(
            "SELECT COUNT(*), COUNT(DISTINCT scene_id), COUNT(DISTINCT facet_id) "
            "FROM facet_observations WHERE region = ? AND polarization = 'VV'",
            (REGION,),
        ).fetchone()
    print(
        json.dumps(
            {
                "completed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "region": REGION,
                "history_window": [start, end],
                "new_rows": inserted,
                "stored_rows": totals[0],
                "stored_scenes": totals[1],
                "stored_facets": totals[2],
                "status": "observations_only_no_scoring_or_page_update",
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
