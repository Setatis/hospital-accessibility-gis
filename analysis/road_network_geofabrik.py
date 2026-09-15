"""从本地 Geofabrik OSM pbf 提取上海路网，供真实路网分析。

背景：Overpass 公共接口对大查询极不稳定（SSL/超时/空返回），而 Geofabrik
提供按区域打包好的 .osm.pbf 文件下载（HTTP 静态文件，稳定可靠）。本项目
下载中国最新 pbf 到本地，再按上海外包框提取路网并缓存为 GPKG 复用。

模块依赖：pyogrio（带 GDAL 的 OSM 驱动），可直接读 pbf，无需额外编译。
"""
from __future__ import annotations

import logging

import geopandas as gpd
from pyogrio import read_dataframe

import config

logger = logging.getLogger(__name__)

# 上海外包框（EPSG:4326），比市域略外扩以包含跨界快速路
SHANGHAI_BBOX = (120.75, 30.60, 122.15, 32.05)

# 路网提取的道路类型：干道 + 本地街/未分级（剔除 service/footway/path/cycleway
# 等细巷，兼顾覆盖真实性与数据规模）
MAIN_HIGHWAYS = {"motorway", "trunk", "primary", "secondary", "tertiary",
                 "residential", "unclassified"}


def extract_shanghai_roads(pbf_path: str | None = None,
                           out_gpkg: str | None = None) -> gpd.GeoDataFrame:
    """从本地 pbf 提取上海主要道路，缓存为 GPKG。

    Args:
        pbf_path: pbf 路径，默认 config.SHANGHAI_PBF。
        out_gpkg: 缓存输出路径，默认 config.SHANGHAI_ROADS_GPKG。

    Returns:
        GeoDataFrame（EPSG:4326），列为 osm_id/name/highway/geometry。
    """
    pbf = pbf_path or str(config.SHANGHAI_PBF)
    out = out_gpkg or str(config.SHANGHAI_ROADS_GPKG)

    # 已有缓存则直接复用
    import os
    if os.path.exists(out):
        roads = gpd.read_file(out)
        if not roads.empty:
            logger.info("复用上海路网缓存: %s（%d 条线）", out, len(roads))
            return roads
    if not os.path.exists(pbf):
        raise FileNotFoundError(f"未找到 OSM pbf: {pbf}")

    logger.info("从 %s 提取上海主要道路（bbox=%s）…", pbf, SHANGHAI_BBOX)
    df = read_dataframe(str(pbf), layer="lines", bbox=SHANGHAI_BBOX)
    hw = df[df["highway"].isin(MAIN_HIGHWAYS) & ~df.geometry.is_empty].copy()
    hw = hw[["osm_id", "name", "highway", "geometry"]]
    hw = hw.set_crs(config.CRS_WGS84, allow_override=True)
    logger.info("提取到上海主要道路 %d 条", len(hw))

    out_dir = os.path.dirname(out)
    os.makedirs(out_dir, exist_ok=True)
    hw.to_file(out, driver="GPKG")
    logger.info("上海路网已缓存: %s", out)
    return hw


def load_or_raise() -> gpd.GeoDataFrame:
    """加载上海真实路网；若不可用则抛异常（由调用方决定回退）。"""
    return extract_shanghai_roads()