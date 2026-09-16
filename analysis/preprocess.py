from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd

import config


def _clean_gdf(gdf: gpd.GeoDataFrame, gname: str = "name") -> gpd.GeoDataFrame:
    """通用清洗：保留有效几何、非空名称、非空几何，并重置索引。"""
    gdf = gdf.copy()
    if "geometry" in gdf.columns:
        gdf = gdf[gdf["geometry"].notna() & gdf.geometry.is_valid]
    if gdf.empty:
        return gdf
    gdf = gdf.reset_index(drop=True)
    return gdf


def clean_districts(districts: gpd.GeoDataFrame, target_crs: str = config.CRS_UTM) -> gpd.GeoDataFrame:
    """清洗行政区：有效几何、非空名称，投影到 target_crs。"""
    districts = _clean_gdf(districts)
    if districts.empty:
        raise ValueError("行政区数据为空，无法继续")
    districts = districts[districts["name"].notna()].copy()
    districts = districts.to_crs(target_crs)
    # 生成唯一标识
    districts["district_id"] = range(1, len(districts) + 1)
    return districts


def clean_hospitals(hospitals: gpd.GeoDataFrame, target_crs: str = config.CRS_UTM) -> gpd.GeoDataFrame:
    """清洗医院：有效点、名称填充、投影到 target_crs。"""
    hospitals = _clean_gdf(hospitals, "name")
    if hospitals.empty:
        raise ValueError("医院数据为空，无法继续")
    if "name" not in hospitals.columns:
        hospitals["name"] = "未知医院"
    hospitals["name"] = hospitals["name"].fillna("未知医院").astype(str)
    hospitals = hospitals.to_crs(target_crs)
    # 生成唯一标识
    hospitals["hospital_id"] = range(1, len(hospitals) + 1)
    return hospitals


def assign_district(hospitals: gpd.GeoDataFrame, districts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """空间连接：为每家医院标注其所在行政区名称（保留投影坐标系）。"""
    hospitals = hospitals.copy()
    # 若原始数据已带 district / district_id 列，先移除（以空间连接结果为准）
    for col in ("district", "district_id"):
        if col in hospitals.columns:
            hospitals = hospitals.drop(columns=[col])

    joined = gpd.sjoin(
        hospitals,
        districts[["district_id", "name", "geometry"]],
        how="left",
        predicate="within",
    )
    # sjoin 会产生 name_left / name_right 与 index 列，整理列名
    joined = joined.rename(columns={"name_right": "district"})
    if "name_left" in joined.columns:
        joined["name"] = joined["name_left"]
    # 落在任何区外的医院标记为“区外”
    joined["district"] = joined["district"].fillna("未知/区外")
    joined["district_id"] = joined["district_id"].fillna(-1).astype(int)
    joined = joined.drop(columns=[c for c in joined.columns
                                  if c in ("index_left", "index_right", "name_left")], errors="ignore")
    # 防御：合并重名列为单个，避免写出报错
    name_counts = joined.columns.value_counts()
    dup = [c for c, n in name_counts.items() if n > 1]
    for c in dup:
        joined = joined[~joined.columns.duplicated()]
    return joined.reset_index(drop=True)


def prepare(districts_raw, hospitals_raw, out_dir=None) -> dict:
    """一步完成的预处理管线，返回投影后的行政区、医院及连接结果。"""
    out_dir = config.INTERMEDIATE_DIR if out_dir is None else out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    districts = clean_districts(districts_raw)
    hospitals = clean_hospitals(hospitals_raw)
    hospitals = assign_district(hospitals, districts)

    # 落盘中间结果
    districts_path = out_dir / "districts_utm.gpkg"
    hospitals_path = out_dir / "hospitals_utm.gpkg"
    districts.to_file(districts_path, driver="GPKG")
    hospitals.to_file(hospitals_path, driver="GPKG")

    return {
        "districts": districts,
        "hospitals": hospitals,
        "districts_path": str(districts_path),
        "hospitals_path": str(hospitals_path),
    }