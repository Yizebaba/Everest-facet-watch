"""Independent Langtang Sentinel-1 monitor and research-snapshot publisher."""
import json
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path


DATA = Path("/app/data")
CONFIG = Path(os.environ.get("FACET_WATCH_CONFIG", "/app/config/monitor.json"))
DATABASE = DATA / "facet-watch.sqlite3"
FACETS = DATA / "references" / "langtang_facets.geojson"
REGION = "langtang"
POLARIZATION = "VV"


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def initialize_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_scenes (
                provider TEXT NOT NULL,
                scene_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY (provider, scene_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS facet_observations (
                region TEXT NOT NULL,
                facet_id TEXT NOT NULL,
                scene_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                orbit INTEGER,
                platform TEXT,
                polarization TEXT NOT NULL,
                value_db REAL NOT NULL,
                valid_pixels INTEGER NOT NULL,
                PRIMARY KEY (region, facet_id, scene_id, polarization)
            )
            """
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(facet_observations)")
        }
        # The first isolated prototype created an empty table before region and
        # platform were introduced. Migrate that local schema without touching
        # observations from any other project.
        if "region" not in columns:
            connection.execute(
                "ALTER TABLE facet_observations ADD COLUMN region TEXT NOT NULL DEFAULT 'unknown'"
            )
        if "platform" not in columns:
            connection.execute("ALTER TABLE facet_observations ADD COLUMN platform TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS facet_observations_region_time "
            "ON facet_observations (region, acquired_at)"
        )


def earth_engine_credentials():
    """Load the isolated OAuth token without repeating the rejected scope set."""
    import ee.oauth
    from google.oauth2.credentials import Credentials

    arguments = ee.oauth.get_credentials_arguments()
    arguments.pop("scopes", None)
    return Credentials(None, **arguments), arguments["quota_project_id"]


def timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def store_scene_rows(connection: sqlite3.Connection, provider: str, scene_id: str, acquired_at: str, rows: list[tuple]) -> int:
    """Persist one fully extracted scene and report only new observation rows."""
    before_observations = connection.total_changes
    connection.executemany(
        "INSERT OR IGNORE INTO facet_observations (region, facet_id, scene_id, acquired_at, orbit, platform, polarization, value_db, valid_pixels) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    inserted = connection.total_changes - before_observations
    connection.execute(
        "INSERT OR REPLACE INTO processed_scenes (provider, scene_id, acquired_at, processed_at) VALUES (?, ?, ?, ?)",
        (provider, scene_id, acquired_at, utc_now()),
    )
    connection.commit()
    return inserted


def ingest_new_langtang_scenes(config: dict) -> dict:
    """Store complete new descending-VV scenes for the fixed Langtang facets."""
    import ee

    if not FACETS.exists():
        raise RuntimeError("Missing fixed Langtang facets. Run initialize.py first.")
    lookback_days = int(config.get("scene_lookback_days", 60))
    batch_size = int(config["facet_batch_size"])
    layer = json.loads(FACETS.read_text(encoding="utf-8"))
    features = layer["features"]
    with sqlite3.connect(DATABASE) as connection:
        latest = connection.execute(
            "SELECT MAX(acquired_at) FROM facet_observations WHERE region = ? AND polarization = ?",
            (REGION, POLARIZATION),
        ).fetchone()[0]
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT scene_id FROM facet_observations WHERE region = ? AND polarization = ?",
                (REGION, POLARIZATION),
            )
        }
    start = datetime(2020, 1, 1, tzinfo=UTC) if latest is None else datetime.fromisoformat(latest.replace("Z", "+00:00")) - timedelta(days=lookback_days)
    credentials, project = earth_engine_credentials()
    ee.Initialize(credentials=credentials, project=project)
    coordinates = [point for feature in features for point in feature["geometry"]["coordinates"][0]]
    bounds = ee.Geometry.MultiPoint(coordinates).bounds()
    scenes = (
        ee.ImageCollection(config["collection"])
        .filterBounds(bounds)
        .filterDate(timestamp(start), timestamp(datetime.now(UTC) + timedelta(days=1)))
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", POLARIZATION))
        .filter(ee.Filter.eq("orbitProperties_pass", "DESCENDING"))
        .sort("system:time_start")
    )
    candidates = [scene_id for scene_id in scenes.aggregate_array("system:index").getInfo() if scene_id not in existing]
    if not candidates:
        return {"latest_before_check": latest, "scenes_found": 0, "scenes_ingested": 0, "rows_inserted": 0}

    reducer = ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True)
    ingested = rows_inserted = 0
    with sqlite3.connect(DATABASE) as connection:
        for scene_id in candidates:
            image = ee.Image(scenes.filter(ee.Filter.eq("system:index", scene_id)).first()).select(POLARIZATION)
            acquired_at = image.date().format("YYYY-MM-dd'T'HH:mm:ss'Z'").getInfo()
            orbit = image.get("relativeOrbitNumber_start").getInfo()
            platform = image.get("platform_number").getInfo()
            rows = []
            for offset in range(0, len(features), batch_size):
                subset = features[offset : offset + batch_size]
                facets = ee.FeatureCollection(
                    [ee.Feature(ee.Geometry(feature["geometry"]), {"facet_id": feature["properties"]["facet_id"]}) for feature in subset]
                )
                for feature in image.reduceRegions(facets, reducer, 30).getInfo()["features"]:
                    properties = feature["properties"]
                    if properties.get("mean") is not None:
                        rows.append((REGION, properties["facet_id"], scene_id, acquired_at, orbit, platform, POLARIZATION, properties["mean"], properties.get("count", 0)))
            rows_inserted += store_scene_rows(connection, config["provider"], scene_id, acquired_at, rows)
            ingested += 1
    return {"latest_before_check": latest, "scenes_found": len(candidates), "scenes_ingested": ingested, "rows_inserted": rows_inserted}


def refresh_langtang_snapshot() -> None:
    from build_langtang_orbit_state import main as build_state
    from publish_langtang_snapshot import main as publish_snapshot
    from score_langtang import main as score

    score()
    build_state()
    publish_snapshot()


def refresh_himalaya_batch_status() -> dict:
    """Advance official-style batch phases only after their prerequisites exist."""
    from himalaya_batch import main as batch
    import himalaya_batch

    batch()
    import ee

    tasks = himalaya_batch.task_records()
    failures = [task for task in tasks if task["state"] == "FAILED" and task.get("error_message") != "Table is empty."]
    write_json(DATA / "himalaya-failures.json", {"updated_at": utc_now(), "failures": failures, "empty_blocks": [task for task in tasks if task.get("error_message") == "Table is empty."]})
    if failures:
        return {"checked_at": utc_now(), "tasks": tasks, "status": "blocked_by_failed_tasks", "failures": len(failures)}
    tier1_assets = [f"tier1_himalaya_{year}" for year in range(2020, 2027)]
    if all(himalaya_batch.asset_exists(f"{himalaya_batch.ASSET_ROOT}/{name}") for name in tier1_assets):
        output = DATA / "himalaya.json"
        if output.exists():
            return {"checked_at": utc_now(), "tasks": tasks, "pipeline": {"status": "published", "output": str(output)}}
        from himalaya_pipeline import main as pipeline
        result = pipeline()
        return {"checked_at": utc_now(), "tasks": tasks, "pipeline": result}
    return {"checked_at": utc_now(), "tasks": tasks}


def cleanup_temporary_files() -> list[str]:
    """Remove stale atomic-write leftovers without touching usable data products."""
    cutoff = time.time() - 24 * 60 * 60
    removed = []
    for path in DATA.rglob("*.tmp"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(str(path.relative_to(DATA)))
    return removed


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    initialize_database(DATABASE)

    last_langtang_check = 0.0
    while True:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        status = {"checked_at": utc_now(), "provider": config["provider"], "collection": config["collection"], "regions": config["regions"], "notifications": "disabled"}
        try:
            if not config["enabled"]:
                status.update({"status": "disabled", "message": "Automatic Sentinel-1 polling is disabled."})
            elif time.monotonic() - last_langtang_check >= int(config["poll_seconds"]):
                ingestion = ingest_new_langtang_scenes(config)
                last_langtang_check = time.monotonic()
                status.update(ingestion)
                if ingestion["scenes_ingested"]:
                    refresh_langtang_snapshot()
                    status.update({"status": "updated", "message": "New Langtang scenes were ingested, scored, and published without notification."})
                else:
                    status.update({"status": "up_to_date", "message": "No new descending Sentinel-1 VV scenes were available."})
        except Exception as error:
            status.update({"status": "error", "message": str(error)})
        write_json(DATA / "monitor-status.json", status)
        if config.get("himalaya_batch_enabled"):
            try:
                himalaya_status = refresh_himalaya_batch_status()
                himalaya_status["cleanup_removed"] = cleanup_temporary_files()
                write_json(DATA / "himalaya-status.json", himalaya_status)
            except Exception as error:
                write_json(DATA / "himalaya-status.json", {"checked_at": utc_now(), "status": "error", "message": str(error)})
        time.sleep(max(30, int(config.get("himalaya_batch_poll_seconds", 900))))


if __name__ == "__main__":
    main()
