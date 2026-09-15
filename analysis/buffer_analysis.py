"""直线缓冲区覆盖分析。

按驾车/步行可通行的直线距离（10 分钟）对每个医院做圆形缓冲区，
再与各自行政区求交，得到“实际落在该区内的覆盖范围”。

- 驾车半径: DRIVE_RADIUS_M
- 步行半径: WALK_RADIUS_M

输出结果 GeoDataFrame（覆盖范围），并附带每家医院覆盖范围，供覆盖率统计与可视化使用。
"""
from __future__ import annotations

import geopandas as gpd

import config


def build_buffers(hospitals: gpd.GeoDataFrame, mode: str) -> gpd.GeoDataFrame:
    """为每家医院构建 10 分钟圆形缓冲覆盖范围。

    Args:
        hospitals: 投影后的医院点（须含 name、hospital_id）
        mode: "drive" 或 "walk"

    Returns:
        GeoDataFrame，含: hospital_id, name, district, radius, coverage_geometry
    """
    radius = config.DRIVE_RADIUS_M if mode == "drive" else config.WALK_RADIUS_M
    gdf = hospitals.copy()
    # buffer 采用 UTM 单位（米），结果仍为投影坐标系
    gdf["geometry"] = gdf.geometry.buffer(radius)
    gdf["radius"] = radius
    gdf["mode"] = mode
    gdf = gdf.rename(columns={"geometry": "coverage_geometry"})
    # 保留医院名与行政区用于标注
    return gdf[["hospital_id", "name", "district", "radius", "mode", "coverage_geometry"]].copy()


def clip_to_districts(coverage: gpd.GeoDataFrame, districts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """将覆盖范围裁剪到其所属行政区边界内，得到实际有效覆盖。"""
    rows = []
    for _, row in coverage.iterrows():
        rec = row.to_dict()
        cov_geom = rec.pop("coverage_geometry")
        dist_geom = districts.loc[districts["name"] == row["district"], "geometry"]
        if dist_geom.empty:
            rec["geometry"] = cov_geom
            rows.append(rec)
            continue
        dgeom = dist_geom.iloc[0]
        eff = cov_geom.intersection(dgeom)
        rec["geometry"] = eff
        rows.append(rec)
    result = gpd.GeoDataFrame(rows, crs=districts.crs)
    result = result.set_geometry("geometry")
    return result


def run(hospitals: gpd.GeoDataFrame, districts: gpd.GeoDataFrame, mode: str) -> gpd.GeoDataFrame:
    """整条缓冲区分析：建缓冲 + 裁剪到区。返回最终有效覆盖。"""
    coverage = build_buffers(hospitals, mode)
    coverage = clip_to_districts(coverage, districts)
    # 计算实际有效面积（米²）
    coverage["area"] = coverage.geometry.to_crs(config.CRS_UTM).area
    return coverage[coverage.geometry.notna() & ~coverage.geometry.is_empty]