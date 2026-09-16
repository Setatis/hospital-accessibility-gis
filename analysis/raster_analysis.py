from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds

import config


def _rasterize_coverage(coverage_gdf: gpd.GeoDataFrame, districts: gpd.GeoDataFrame,
                        res: float, out_tif: str):
    """将 coverage 栅格化到研究区范围内，写 GeoTiff，返回 (array, transform, crs)。"""
    total_bounds = districts.total_bounds
    minx, miny, maxx, maxy = total_bounds
    width = int(np.ceil((maxx - minx) / res))
    height = int(np.ceil((maxy - miny) / res))
    transform = from_bounds(minx, miny, maxx, maxy, width, height)

    # 覆盖范围用 1 值栅格化；无覆盖默认 0
    shapes = [(geom, 1) for geom in coverage_gdf.geometry if geom is not None]
    raster = rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype="float32",
    )
    with rasterio.open(
        out_tif, "w", driver="GTiff",
        height=height, width=width, count=1,
        dtype=raster.dtype, crs=districts.crs, transform=transform,
    ) as dst:
        dst.write(raster, 1)
    return raster, transform, districts.crs


def zonal_summary(coverage_gdf: gpd.GeoDataFrame, districts: gpd.GeoDataFrame,
                  method: str, mode: str, res: float = None, out_tif: str = None) -> pd.DataFrame:
    """栅格分区统计：计算每行政区栅格覆盖面积与占比。

    Returns DataFrame: method, mode, district, raster_covered_km2, raster_coverage_pct
    """
    res = config.RASTER_RES_M if res is None else res
    if out_tif is None:
        out_tif = config.PROCESSED_DIR / f"coverage_raster_{method}_{mode}.tif"
    if coverage_gdf.empty:
        return pd.DataFrame(columns=["method", "mode", "district", "raster_covered_km2", "raster_coverage_pct"])

    raster, transform, crs = _rasterize_coverage(coverage_gdf, districts, res, str(out_tif))

    rows = []
    # 对每个区，统计其 bounds 内被覆盖(grid==1)的像元
    for _, dr in districts.iterrows():
        dname = dr["name"]
        g = dr["geometry"]
        cell = res * res
        # 用区自身栅格掩码更准确：rst 中落在区几何内的覆盖像元
        from rasterio.features import geometry_mask
        bounds = g.bounds
        cols0 = max(int((bounds[0] - transform.c) / transform.a), 0)
        rows0 = max(int((transform.f - bounds[3]) / -transform.e), 0)
        cols1 = min(int((bounds[2] - transform.c) / transform.a) + 1, raster.shape[1])
        rows1 = min(int((transform.f - bounds[1]) / -transform.e) + 1, raster.shape[0])
        if rows1 <= rows0 or cols1 <= cols0:
            continue
        sub = raster[rows0:rows1, cols0:cols1]
        # 掩码需使用子窗口对应的 transform，否则与 sub 错位
        from rasterio.transform import Affine
        sub_tr = transform * Affine.translation(cols0, rows0)
        mask = geometry_mask([g], out_shape=sub.shape, transform=sub_tr, invert=True)
        covered_cells = int(np.nansum(np.where(mask, np.where(sub == 1, 1.0, 0.0), 0.0)))
        cov_area = covered_cells * cell
        total_area = g.area
        pct = (cov_area / total_area * 100.0) if total_area > 0 else 0.0
        rows.append({
            "method": method, "mode": mode, "district": dname,
            "raster_covered_km2": round(cov_area / 1e6, 3),
            "raster_coverage_pct": round(pct, 2),
        })
    df = pd.DataFrame(rows)
    return df


def run_all(result_gdfs: dict, districts: gpd.GeoDataFrame, out_dir=None) -> pd.DataFrame:
    """对全部 (method, mode) 覆盖结果做栅格分区统计。"""
    out_dir = config.PROCESSED_DIR if out_dir is None else out_dir
    frames = []
    for (method, mode), cov in result_gdfs.items():
        tif = out_dir / f"coverage_raster_{method}_{mode}.tif"
        df = zonal_summary(cov, districts, method, mode, out_tif=str(tif))
        frames.append(df)
    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return table