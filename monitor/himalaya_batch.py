"""Resumable official-style Earth Engine batch setup for Himalaya Tier 1.

The fixed 2,576 facets are recovered from the exported OCHA dashboard, then
stored as 15 Earth Engine assets. Once every asset exists, one descending-VV
seasonal-mean export is submitted per year. This is intentionally a coarse
Tier 1 screen; Tier 2 remains a later, small-candidate validation stage.
"""
import json
import os
from pathlib import Path

import ee

from monitor import earth_engine_credentials


DATA = Path("/app/data")
SOURCE = Path("/app/web/himalaya/index.html")
FACETS = DATA / "references" / "himalaya_facets.geojson"
REGION = [82.0, 26.8, 89.0, 30.2]
BLOCKS_X, BLOCKS_Y = 5, 3
YEARS = range(2020, 2027)
ASSET_ROOT = os.environ.get("FACET_WATCH_HIMALAYA_ASSET_ROOT")


def embedded_payload(html: str) -> dict:
    marker = "const DATA = "
    start = html.index(marker) + len(marker)
    return json.JSONDecoder().raw_decode(html[start:])[0]


def initialize_reference() -> list[dict]:
    if FACETS.exists():
        features = json.loads(FACETS.read_text(encoding="utf-8"))["features"]
        if len(features) == 2576:
            return features
        raise RuntimeError(f"Expected 2576 Himalaya facets, found {len(features)}")
    payload = embedded_payload(SOURCE.read_text(encoding="utf-8"))
    if len(payload["facets"]) != 2576:
        raise RuntimeError(f"Expected 2576 exported Himalaya facets, found {len(payload['facets'])}")
    features = [
        {
            "type": "Feature",
            "properties": {"facet_id": facet["id"], "aspect": facet.get("aspect"), "km2": facet.get("km2")},
            "geometry": {"type": "Polygon", "coordinates": [facet["poly"]]},
        }
        for facet in payload["facets"]
    ]
    FACETS.parent.mkdir(parents=True, exist_ok=True)
    FACETS.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return features


def asset_exists(asset_id: str) -> bool:
    try:
        ee.data.getAsset(asset_id)
        return True
    except ee.ee_exception.EEException:
        return False


def ensure_folder() -> None:
    if not asset_exists(ASSET_ROOT):
        ee.data.createAsset({"type": "Folder"}, ASSET_ROOT)


def active_tasks() -> dict[str, str]:
    return {
        task.get("description", ""): task["state"]
        for task in ee.data.getTaskList()
        if task.get("state") in ("READY", "RUNNING")
    }


def empty_facet_blocks() -> set[str]:
    return {
        task.get("description", "")
        for task in ee.data.getTaskList()
        if task.get("state") == "FAILED" and task.get("error_message") == "Table is empty."
    }


def task_records() -> list[dict]:
    """Return tracked Earth Engine tasks with enough context to repair failures."""
    records = []
    for task in ee.data.getTaskList():
        name = str(task.get("description", ""))
        if not name.startswith(("facets_himalaya_", "tier1_himalaya_")):
            continue
        record = {"id": task.get("id"), "description": name, "state": task.get("state"), "error_message": task.get("error_message")}
        if name.startswith("facets_himalaya_"):
            _, _, x, y = name.split("_")
            west, south, east, north = REGION
            dx, dy = (east - west) / BLOCKS_X, (north - south) / BLOCKS_Y
            record["phase"] = "A"
            record["block"] = [int(x), int(y)]
            record["bounds"] = [west + int(x) * dx, south + int(y) * dy, west + (int(x) + 1) * dx, south + (int(y) + 1) * dy]
        else:
            record["phase"] = "B"
            record["year"] = int(name.rsplit("_", 1)[1])
        records.append(record)
    return sorted(records, key=lambda record: record["description"])


def block_index(feature: dict) -> tuple[int, int]:
    coordinates = feature["geometry"]["coordinates"][0]
    lon = sum(point[0] for point in coordinates) / len(coordinates)
    lat = sum(point[1] for point in coordinates) / len(coordinates)
    west, south, east, north = REGION
    x = min(BLOCKS_X - 1, max(0, int((lon - west) / (east - west) * BLOCKS_X)))
    y = min(BLOCKS_Y - 1, max(0, int((lat - south) / (north - south) * BLOCKS_Y)))
    return x, y


def submit_facets(features: list[dict]) -> dict:
    submitted = ready = empty = waiting = 0
    active = active_tasks()
    empty_blocks = empty_facet_blocks()
    for x in range(BLOCKS_X):
        for y in range(BLOCKS_Y):
            name = f"facets_himalaya_{x}_{y}"
            asset_id = f"{ASSET_ROOT}/{name}"
            if asset_exists(asset_id):
                ready += 1
                continue
            if name in empty_blocks:
                empty += 1
                continue
            if name in active:
                waiting += 1
                continue
            subset = [feature for feature in features if block_index(feature) == (x, y)]
            collection = ee.FeatureCollection(
                [ee.Feature(ee.Geometry(feature["geometry"]), feature["properties"]) for feature in subset]
            )
            ee.batch.Export.table.toAsset(collection, description=name, assetId=asset_id).start()
            submitted += 1
    return {"ready": ready, "empty": empty, "waiting": waiting, "submitted": submitted, "total": BLOCKS_X * BLOCKS_Y}


def merged_facets() -> ee.FeatureCollection:
    collections = [
        ee.FeatureCollection(f"{ASSET_ROOT}/facets_himalaya_{x}_{y}")
        for x in range(BLOCKS_X)
        for y in range(BLOCKS_Y)
        if asset_exists(f"{ASSET_ROOT}/facets_himalaya_{x}_{y}")
    ]
    if not collections:
        raise RuntimeError("No completed Himalaya facet assets are available.")
    merged = collections[0]
    for collection in collections[1:]:
        merged = merged.merge(collection)
    return merged


def submit_tier1() -> dict:
    facets = merged_facets()
    submitted = ready = waiting = 0
    active = active_tasks()
    for year in YEARS:
        name = f"tier1_himalaya_{year}"
        asset_id = f"{ASSET_ROOT}/{name}"
        if asset_exists(asset_id):
            ready += 1
            continue
        if name in active:
            waiting += 1
            continue
        image = (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(ee.Geometry.Rectangle(REGION))
            .filterDate(f"{year}-06-01", f"{year}-08-26")
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(ee.Filter.eq("orbitProperties_pass", "DESCENDING"))
            .select("VV")
            .mean()
        )
        rows = image.reduceRegions(facets, ee.Reducer.mean(), 30).map(
            lambda feature: ee.Feature(feature.geometry().centroid(100), {"facet_id": feature.get("facet_id"), "year": year, "vv_db": feature.get("mean")})
        )
        ee.batch.Export.table.toAsset(rows, description=name, assetId=asset_id).start()
        submitted += 1
    return {"ready": ready, "waiting": waiting, "submitted": submitted, "total": len(YEARS)}


def main() -> None:
    if not ASSET_ROOT:
        raise RuntimeError("FACET_WATCH_HIMALAYA_ASSET_ROOT is required.")
    credentials, project = earth_engine_credentials()
    ee.Initialize(credentials=credentials, project=project)
    ensure_folder()
    facets = initialize_reference()
    phase_a = submit_facets(facets)
    phase_a_done = phase_a["ready"] + phase_a["empty"]
    if phase_a_done + phase_a["waiting"] + phase_a["submitted"] < phase_a["total"]:
        raise RuntimeError("Facet task submission did not cover every block.")
    if phase_a_done < phase_a["total"]:
        print({"phase": "A", "facets": len(facets), "assets": phase_a, "status": "tasks_submitted_wait_for_earth_engine"})
        return
    phase_b = submit_tier1()
    print({"phase": "B", "facets": len(facets), "assets": phase_a, "tier1": phase_b, "status": "tier1_tasks_submitted_or_ready"})


if __name__ == "__main__":
    main()
