"""Extract immutable Langtang facet geometry from the exported OCHA page."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


SOURCE = Path("/app/web/langtang/index.html")
OUTPUT = Path("/app/data/references")


def embedded_payload(html: str) -> dict:
    marker = "const DATA = "
    start = html.index(marker) + len(marker)
    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(html[start:])
    return value


def main() -> None:
    source_bytes = SOURCE.read_bytes()
    payload = embedded_payload(source_bytes.decode("utf-8"))
    facets = payload["facets"]
    if len(facets) != 580:
        raise RuntimeError(f"Expected 580 Langtang facets in exported page, found {len(facets)}")

    features = []
    for facet in facets:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "facet_id": facet["id"],
                    "aspect": facet.get("aspect"),
                    "km2": facet.get("km2"),
                },
                "geometry": {"type": "Polygon", "coordinates": [facet["poly"]]},
            }
        )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    layer = {
        "type": "FeatureCollection",
        "name": "langtang_facets_exported_snapshot",
        "features": features,
    }
    (OUTPUT / "langtang_facets.geojson").write_text(
        json.dumps(layer, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Exported local Facet Watch Langtang page; geometry originally embedded by OCHA experimental dashboard build.",
        "source_file": "/app/web/langtang/index.html",
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "facet_count": len(features),
        "fields": ["facet_id", "aspect", "km2", "geometry"],
        "limitations": [
            "This is a fixed reference layer only.",
            "The public OCHA repository does not include the original facet GeoJSON or per-acquisition history CSV.",
            "No Sentinel-1 observation or historical baseline is created by this command.",
        ],
    }
    (OUTPUT / "langtang_facets.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(features)} fixed Langtang facets to {OUTPUT}")


if __name__ == "__main__":
    main()
