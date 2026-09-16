from __future__ import annotations

import geopandas as gpd
import pandas as pd

import config


def _district_area_m2(districts: gpd.GeoDataFrame) -> pd.Series:
    """返回每个区面积（米²），索引为区名。"""
    s = districts.set_index("name")["geometry"].area
    return s


def district_coverage_stats(
    coverage: gpd.GeoDataFrame,
    districts: gpd.GeoDataFrame,
    method: str,
    mode: str,
) -> pd.DataFrame:
    """计算覆盖率统计表。coverage 需为投影后的覆盖范围（含 district、geometry）。

    Returns DataFrame with columns:
        method, mode, district, total_area_km2, covered_area_km2, coverage_pct,
        hospital_count
    """
    total_area = _district_area_m2(districts)

    # 每家医院覆盖范围先按区 dissolve 并集：一个区对应一个并集(Multi)Polygon
    union_by_district: dict = {}
    if not coverage.empty:
        dissolved = coverage.dissolve(by="district")[["geometry"]]
        union_by_district = dict(zip(dissolved.index, dissolved["geometry"]))

    rows = []
    for dname, total_m2 in total_area.items():
        cov_poly = union_by_district.get(dname)
        cov_area = cov_poly.area if cov_poly is not None else 0.0
        pct = (cov_area / total_m2 * 100.0) if total_m2 > 0 else 0.0
        hosp_count = int((coverage["district"] == dname).sum())
        rows.append({
            "method": method,
            "mode": mode,
            "district": dname,
            "total_area_km2": round(total_m2 / 1e6, 3),
            "covered_area_km2": round(cov_area / 1e6, 3),
            "coverage_pct": round(pct, 2),
            "hospital_count": hosp_count,
        })
    return pd.DataFrame(rows)


def build_stats_table(
    results: dict,
    districts: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """汇总所有(method, mode)组合的覆盖率统计表。

    Args:
        results: {(method, mode): coverage_gdf}
    """
    frames = []
    for (method, mode), cov in results.items():
        frames.append(district_coverage_stats(cov, districts, method, mode))
    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return table


def export(table: pd.DataFrame, districts: gpd.GeoDataFrame, out_dir=None) -> dict:
    """导出统计表 CSV 与带属性的行政区 GeoJSON。"""
    out_dir = config.STATS_DIR if out_dir is None else out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "coverage_stats.csv"
    table.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # 为行政区附上“驾车覆盖”和“步行覆盖”指标（取 buffer 方案作示例附在几何上）
    gjson = districts.copy()
    summary = table.groupby("district").agg(
        avg_coverage_pct=("coverage_pct", "mean"),
        total_covered_km2=("covered_area_km2", "sum"),
        total_area_km2=("total_area_km2", "mean"),
    ).reset_index()
    # 行政区用 name 与统计中的 district 对齐
    gjson["district"] = gjson["name"]
    gjson = gjson.merge(summary, how="left", on="district")
    gjson = gjson.to_crs(config.CRS_WGS84)
    geojson_path = out_dir / "districts_coverage.geojson"
    gjson.to_file(geojson_path, driver="GeoJSON")

    return {"csv": str(csv_path), "geojson": str(geojson_path)}