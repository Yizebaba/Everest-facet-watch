"""Automated Phase C, Tier 1/Tier 2 validation, and Himalaya JSON publishing.

This follows the OCHA two-tier order: all-facet seasonal means first, then the
full per-acquisition detector only for the 40 most anomalous Tier 1 candidates.
Each phase is idempotent and raises on an incomplete upstream asset instead of
publishing a partial result.
"""
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import ee
import numpy as np
import pandas as pd

from himalaya_batch import ASSET_ROOT, FACETS, REGION, YEARS
from monitor import earth_engine_credentials


DATA = Path("/app/data")
SOURCE = Path("/app/web/himalaya/index.html")
TIER1 = DATA / "himalaya_tier1.csv"
TIER2 = DATA / "himalaya_tier2.csv"
OUTPUT = DATA / "himalaya.json"
TOP_N = 40
SEASON_START, SEASON_END = 152, 237
DOY_WINDOW, MIN_POOL, RUN_LENGTH = 12, 10, 3
TIERS = ((1, "critical"), (5, "elevated"), (15, "watch"), (100, "quiet"))


def read_features() -> list[dict]:
    return json.loads(FACETS.read_text(encoding="utf-8"))["features"]


def read_exported_facets() -> list[dict]:
    marker = "const DATA = "
    html = SOURCE.read_text(encoding="utf-8")
    return json.JSONDecoder().raw_decode(html[html.index(marker) + len(marker) :])[0]["facets"]


def asset_exists(asset_id: str) -> bool:
    try:
        ee.data.getAsset(asset_id)
        return True
    except ee.ee_exception.EEException:
        return False


def read_asset_rows(asset_id: str) -> list[dict]:
    collection = ee.FeatureCollection(asset_id)
    total = collection.size().getInfo()
    rows = []
    values = collection.toList(total)
    for offset in range(0, total, 4000):
        rows.extend(feature["properties"] for feature in ee.FeatureCollection(values.slice(offset, offset + 4000)).getInfo()["features"])
    return rows


def collect_tier1() -> pd.DataFrame:
    missing = [year for year in YEARS if not asset_exists(f"{ASSET_ROOT}/tier1_himalaya_{year}")]
    if missing:
        raise RuntimeError(f"Tier 1 assets are incomplete: missing years {missing}")
    rows = []
    for year in YEARS:
        rows.extend(read_asset_rows(f"{ASSET_ROOT}/tier1_himalaya_{year}"))
    frame = pd.DataFrame(rows)[["facet_id", "year", "vv_db"]].dropna()
    frame["year"] = frame["year"].astype(int)
    frame["vv_db"] = frame["vv_db"].astype(float)
    expected = {feature["properties"]["facet_id"] for feature in read_features()}
    observed = set(frame["facet_id"])
    missing_facets = expected - observed
    if len(observed) < 2400 or len(missing_facets) > 176:
        raise RuntimeError(f"Tier 1 coverage is insufficient: {len(observed)}/2576 facets with data")
    per_year = frame.groupby("year")["facet_id"].nunique().to_dict()
    if any(per_year.get(year, 0) < 2400 for year in YEARS):
        raise RuntimeError(f"Tier 1 year coverage is insufficient: {per_year}")
    temporary = TIER1.with_suffix(".csv.tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(TIER1)
    return frame


def centroids(features: list[dict]) -> dict[str, tuple[float, float]]:
    return {
        feature["properties"]["facet_id"]: (
            sum(point[0] for point in feature["geometry"]["coordinates"][0]) / len(feature["geometry"]["coordinates"][0]),
            sum(point[1] for point in feature["geometry"]["coordinates"][0]) / len(feature["geometry"]["coordinates"][0]),
        )
        for feature in features
    }


def tier_for(percentile: float) -> str:
    return next(name for limit, name in TIERS if percentile < limit or limit == 100)


def tier1_scores(frame: pd.DataFrame, features: list[dict]) -> pd.DataFrame:
    rows = []
    for facet_id, group in frame.groupby("facet_id", sort=False):
        values = group.set_index("year")["vv_db"]
        if len(values) < 5:
            continue
        for year, value in values.items():
            others = values.drop(year)
            if others.std(ddof=1) > 0.05:
                rows.append({"facet_id": facet_id, "year": year, "z1": (value - others.mean()) / max(others.std(ddof=1), 0.3)})
    score = pd.DataFrame(rows)
    points = centroids(features)
    score["cell"] = score["facet_id"].map(lambda facet_id: f"{points[facet_id][0]:.0f}_{points[facet_id][1]:.0f}")
    cell_median = score.groupby(["cell", "year"])["z1"].transform("median")
    cell_size = score.groupby(["cell", "year"])["z1"].transform("size")
    domain_median = score.groupby("year")["z1"].transform("median")
    score["z1_adjusted"] = score["z1"] - cell_median.where(cell_size >= 15, domain_median)
    return score


def selected_tier2_facets(score: pd.DataFrame) -> list[str]:
    current = score[score["year"] == max(YEARS)].sort_values("z1_adjusted")
    if len(current) < TOP_N:
        raise RuntimeError(f"Only {len(current)} Tier 1 facets can be ranked; need {TOP_N}")
    return current.head(TOP_N)["facet_id"].tolist()


def extract_tier2(selected: list[str], features: list[dict]) -> pd.DataFrame:
    lookup = {feature["properties"]["facet_id"]: feature for feature in features}
    collection = ee.FeatureCollection([ee.Feature(ee.Geometry(lookup[facet_id]["geometry"]), {"facet_id": facet_id}) for facet_id in selected])
    reducer = ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True)
    rows = []
    for year in YEARS:
        images = (
            ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(collection.geometry().bounds())
            .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(ee.Filter.eq("orbitProperties_pass", "DESCENDING"))
            .select("VV")
        )
        def per_image(image):
            return image.reduceRegions(collection, reducer, 30).map(
                lambda feature: ee.Feature(
                    None,
                    {
                        "facet_id": feature.get("facet_id"),
                        "scene_id": image.get("system:index"),
                        "acquired_at": image.date().format("YYYY-MM-dd'T'HH:mm:ss'Z'"),
                        "orbit": image.get("relativeOrbitNumber_start"),
                        "vv_db": feature.get("mean"),
                        "valid_pixels": feature.get("count"),
                    },
                )
            )

        extracted = ee.FeatureCollection(images.map(per_image)).flatten().filter(
            ee.Filter.notNull(["vv_db"])
        ).getInfo()["features"]
        rows.extend(feature["properties"] for feature in extracted)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("Tier 2 extraction returned no observations.")
    temporary = TIER2.with_suffix(".csv.tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(TIER2)
    return frame


def worst_run(values: pd.Series) -> float | None:
    if len(values) < RUN_LENGTH:
        return None
    return float(min(values.iloc[index : index + RUN_LENGTH].max() for index in range(len(values) - RUN_LENGTH + 1)))


def tier2_scores(frame: pd.DataFrame) -> dict[str, float]:
    frame["acquired_at"] = pd.to_datetime(frame["acquired_at"])
    frame["year"] = frame["acquired_at"].dt.year
    frame["doy"] = frame["acquired_at"].dt.dayofyear
    frame["scene_date"] = frame["acquired_at"].dt.date.astype(str)
    quality = frame[frame["valid_pixels"] >= 0.8 * frame.groupby(["facet_id", "orbit"])["valid_pixels"].transform("max")].copy()
    scored = []
    for _, group in quality.groupby(["facet_id", "orbit"], sort=False):
        group = group.sort_values("acquired_at").copy()
        values, years, doys = group["vv_db"].to_numpy(), group["year"].to_numpy(), group["doy"].to_numpy()
        z = np.full(len(group), np.nan)
        for index in range(len(group)):
            difference = np.minimum(np.abs(doys - doys[index]), 365 - np.abs(doys - doys[index]))
            pool = (difference <= DOY_WINDOW) & (years != years[index])
            if pool.sum() >= MIN_POOL and values[pool].std(ddof=1) > 0:
                z[index] = (values[index] - values[pool].mean()) / values[pool].std(ddof=1)
        group["z"] = z
        scored.append(group)
    score = pd.concat(scored, ignore_index=True).dropna(subset=["z"])
    score["z_adjusted"] = score["z"] - score.groupby(["orbit", "scene_date"])["z"].transform("median")
    season = score[(score["doy"] >= SEASON_START) & (score["doy"] <= SEASON_END)]
    values = {}
    for (facet_id, year), group in season.groupby(["facet_id", "year"]):
        run = worst_run(group.sort_values("acquired_at")["z_adjusted"])
        if run is not None:
            values.setdefault(facet_id, {})[int(year)] = run
    return {facet_id: yearly.get(max(YEARS)) for facet_id, yearly in values.items()}


def publish(score: pd.DataFrame, features: list[dict], tier2: dict[str, float]) -> None:
    exported = {facet["id"]: facet for facet in read_exported_facets()}
    history = score[score["year"] < max(YEARS)]["z1_adjusted"].to_numpy()
    current = score[score["year"] == max(YEARS)].set_index("facet_id")["z1_adjusted"].to_dict()
    years = score.pivot(index="facet_id", columns="year", values="z1_adjusted").to_dict("index")
    facets = []
    tiers = {"critical": 0, "elevated": 0, "watch": 0, "quiet": 0, "nodata": 0}
    for feature in features:
        facet_id = feature["properties"]["facet_id"]
        live = current.get(facet_id)
        if live is None:
            tier, percentile = "nodata", None
        else:
            percentile = round(100 * float((history <= live).mean()), 1)
            tier = tier_for(percentile)
        tiers[tier] += 1
        original = exported.get(facet_id, {})
        facets.append({"id": facet_id, "aspect": feature["properties"].get("aspect"), "km2": feature["properties"].get("km2"), "tier": tier, "live": live, "pct": percentile, "years": {str(year): round(value, 2) for year, value in years.get(facet_id, {}).items() if pd.notna(value) and year < max(YEARS)}, "tier2": tier2.get(facet_id), "poly": feature["geometry"]["coordinates"][0], "drop": original.get("drop"), "lake": original.get("lake"), "pop": original.get("pop"), "chain": original.get("chain")})
    payload = {"schema_version": 1, "product": "Himalaya two-tier Sentinel-1 research snapshot", "status": "research_only_not_warning", "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "tier1_year": max(YEARS), "method": {"tier1": "All-facet descending-VV seasonal mean screen, 2020-2026.", "tier2": "Full per-acquisition validation is calculated only for the 40 lowest Tier 1 candidates.", "limitations": "Tier 1 is a coarse screen, not a warning. Tier 2 is available only for selected candidates."}, "tiers": tiers, "facets": facets}
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)


def main() -> dict:
    credentials, project = earth_engine_credentials()
    ee.Initialize(credentials=credentials, project=project)
    features = read_features()
    frame = collect_tier1()
    score = tier1_scores(frame, features)
    selected = selected_tier2_facets(score)
    tier2 = tier2_scores(extract_tier2(selected, features))
    publish(score, features, tier2)
    return {"status": "published", "tier1_rows": len(frame), "tier2_candidates": len(selected), "output": str(OUTPUT)}


if __name__ == "__main__":
    print(main())
