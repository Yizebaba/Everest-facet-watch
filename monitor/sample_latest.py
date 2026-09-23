"""Controlled one-scene VV extraction for the fixed Langtang reference layer.

This is intentionally a manual command, not the resident monitor loop. It
implements the same per-scene, per-facet mean/count reduction used by OCHA's
live_extract.py, but writes an isolated sample artifact only.
"""
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import ee

from monitor import earth_engine_credentials


FACETS = Path("/app/data/references/langtang_facets.geojson")
OUTPUT = Path("/app/data/samples")
BATCH_SIZE = 60


def main() -> None:
    if not FACETS.exists():
        raise RuntimeError("Run initialize.py before sampling a Sentinel-1 scene.")

    credentials, project = earth_engine_credentials()
    ee.Initialize(credentials=credentials, project=project)
    layer = json.loads(FACETS.read_text(encoding="utf-8"))
    features = layer["features"]
    coordinates = [point for feature in features for point in feature["geometry"]["coordinates"][0]]
    bounds = ee.Geometry.MultiPoint(coordinates).bounds()
    scenes = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(bounds)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.eq("orbitProperties_pass", "DESCENDING"))
    )
    image = ee.Image(scenes.sort("system:time_start", False).first()).select("VV")
    scene_id = image.get("system:index").getInfo()
    acquired_at = image.date().format("YYYY-MM-dd'T'HH:mm:ss'Z'").getInfo()
    orbit = image.get("relativeOrbitNumber_start").getInfo()
    platform = image.get("platform_number").getInfo()
    reducer = ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True)

    rows = []
    for offset in range(0, len(features), BATCH_SIZE):
        subset = features[offset : offset + BATCH_SIZE]
        collection = ee.FeatureCollection(
            [
                ee.Feature(ee.Geometry(feature["geometry"]), {"facet_id": feature["properties"]["facet_id"]})
                for feature in subset
            ]
        )
        result = image.reduceRegions(collection, reducer, 30).getInfo()["features"]
        for feature in result:
            properties = feature["properties"]
            # A single selected VV band is returned as mean/count. OCHA's
            # two-band reducer used VV_mean/VV_count instead.
            if properties.get("mean") is not None:
                rows.append(
                    {
                        "facet_id": properties["facet_id"],
                        "vv_db": round(properties["mean"], 4),
                        "valid_pixels": properties.get("count", 0),
                    }
                )
        print(f"batch {offset // BATCH_SIZE + 1}: {len(result)} facets", flush=True)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT / "langtang_latest_descending_vv.csv"
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["facet_id", "vv_db", "valid_pixels"])
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": "Manual one-scene validation sample. Not a live state or baseline.",
        "collection": "COPERNICUS/S1_GRD",
        "scene_id": scene_id,
        "acquired_at": acquired_at,
        "relative_orbit": orbit,
        "platform": platform,
        "pass": "DESCENDING",
        "polarization": "VV",
        "facet_layer": str(FACETS),
        "facet_count": len(features),
        "rows_with_data": len(rows),
        "reducer": "mean and count at 30 m",
        "output": str(destination),
    }
    (OUTPUT / "langtang_latest_descending_vv.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=True))


if __name__ == "__main__":
    main()
