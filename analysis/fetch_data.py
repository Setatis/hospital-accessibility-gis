"""从 OpenStreetMap (Overpass API) 下载上海市行政区边界、医院 POI。

接口说明：
- download_districts(): 下载行政区边界
- download_hospitals(): 下载医院点位（node 与 way 中心点）
- download_road_network(): 下载路网（由 osmnx 负责，见 network_analysis）

若下载失败（网络不通 / 超时 / 数据为空），会抛出异常，由 main.py 捕获并
回退到 generate_demo_data 合成数据。
"""
from __future__ import annotations

import logging
import time
import requests
import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

import config

logger = logging.getLogger(__name__)

# 上海市近似外包框，用于裁剪非上海要素
SHANGHAI_BBOX = (120.80, 30.60, 122.10, 32.00)


def _overpass(query: str) -> dict:
    """向 Overpass API 提交查询，返回解析后的 JSON 结构。

    按 config.OVERPASS_API 中的实例顺序尝试；每个实例失败后按
    config.OVERPASS_RETRIES 重试（应对公共服务负载波动），全部失败时抛出最后一个异常。
    """
    last_exc = None
    for api in config.OVERPASS_API:
        for attempt in range(1, config.OVERPASS_RETRIES + 1):
            try:
                resp = requests.post(
                    api,
                    data={"data": query},
                    timeout=config.FETCH_TIMEOUT_S,
                    headers={"User-Agent": "shanghai-hospital-gis/1.0 (analysis demo)"},
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa
                last_exc = exc
                logger.warning(
                    "Overpass 请求失败: %s（实例 %s，第 %d/%d 次尝试）",
                    exc, api, attempt, config.OVERPASS_RETRIES,
                )
                if attempt < config.OVERPASS_RETRIES:
                    time.sleep(1)  # 重试前短暂等待，避免瞬时过载
    raise last_exc


def _elements_to_gdf(elements: list) -> gpd.GeoDataFrame:
    """将 Overpass 返回的 element 列表转为 GeoDataFrame（node 用坐标，way 直接 geometry）。"""
    features = []
    for el in elements:
        geom = None
        if el.get("type") == "node":
            if "lat" in el and "lon" in el:
                geom = shape({"type": "Point", "coordinates": [el["lon"], el["lat"]]})
        elif el.get("type") in ("way", "relation") and "geometry" in el:
            # Overpass out geom 会给 way/relation 带 geometry
            coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
            if el["type"] == "way":
                if len(coords) == 1:
                    geom = shape({"type": "Point", "coordinates": coords[0]})
                elif len(coords) == 2:
                    geom = shape({"type": "LineString", "coordinates": coords})
                else:
                    if el.get("geometry") and el["geometry"][0] == el["geometry"][-1]:
                        geom = shape({"type": "Polygon", "coordinates": [coords]})
                    else:
                        geom = shape({"type": "LineString", "coordinates": coords})
        if geom is None:
            continue
        props = {k: v for k, v in el.get("tags", {}).items() if v}
        props["osm_id"] = el.get("id")
        props["osm_type"] = el.get("type")
        features.append({"properties": props, "geometry": geom})
    gdf = gpd.GeoDataFrame.from_features(features)
    if gdf.shape[0] == 0:
        gdf = gpd.GeoDataFrame(columns=list(pd.Series([f["properties"] for f in features]).columns)
                               if features else ["geometry"])
    gdf = gdf.set_geometry("geometry").set_crs(config.CRS_WGS84, allow_override=True)
    return gdf


def _clip_to_bounds(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """按上海外包框裁剪要素。"""
    from shapely.geometry import box
    bbox = box(*SHANGHAI_BBOX)
    return gdf[gdf.intersects(bbox)].copy()


def download_districts_datav(out_path=None) -> gpd.GeoDataFrame:
    """从阿里 DataV 下载上海市行政区边界（权威 16 区县，GeoJSON，CGCS2000≈WGS84）。"""
    resp = requests.get(
        config.DATAV_AREA_URL,
        timeout=config.FETCH_TIMEOUT_S,
        headers={"User-Agent": "Mozilla/5.0 (shanghai-hospital-gis analysis)"},
    )
    resp.raise_for_status()
    fc = resp.json()
    features = fc.get("features", [])
    if not features:
        raise RuntimeError("DataV 未返回任何上海市行政区数据")

    gdf = gpd.GeoDataFrame.from_features(features)
    gdf = gdf.set_crs(config.CRS_WGS84, allow_override=True)
    # 标准化字段：name / adcode
    gdf["name"] = gdf.get("name", gdf.index.astype(str))
    gdf["admin_level"] = "6"
    keep = [c for c in ("name", "adcode", "level", "admin_level", "geometry") if c in gdf.columns]
    gdf = gdf[keep].copy()

    if out_path:
        gdf.to_file(out_path, driver="GPKG")
    return gdf


def download_districts(out_path=None) -> gpd.GeoDataFrame:
    """下载上海行政区边界。

    优先使用阿里 DataV 权威区划数据（国内直连稳定）；失败时回退到 Overpass。
    """
    try:
        gdf = download_districts_datav(out_path)
        return gdf
    except Exception as exc:  # noqa
        logger.warning("DataV 行政区下载失败，回退 Overpass。原因: %s", exc)
        return _download_districts_osm(out_path)


def _download_districts_osm(out_path=None) -> gpd.GeoDataFrame:
    """Overpass 回退：下载上海行政区边界（区级 admin_level=6）。"""
    query = f"""
    [out:json][timeout:{config.OVERPASS_TIMEOUT}];
    (
      rel["boundary"="administrative"]["admin_level"="4"]["name"="上海市"]({SHANGHAI_BBOX[1]},{SHANGHAI_BBOX[0]},{SHANGHAI_BBOX[3]},{SHANGHAI_BBOX[2]});
      rel["boundary"="administrative"]["admin_level"="6"]({SHANGHAI_BBOX[1]},{SHANGHAI_BBOX[0]},{SHANGHAI_BBOX[3]},{SHANGHAI_BBOX[2]});
    );
    out body geom;
    """
    data = _overpass(query)
    gdf = _elements_to_gdf(data.get("elements", []))
    gdf = _clip_to_bounds(gdf)
    if gdf.empty:
        raise RuntimeError("Overpass 未返回任何上海行政区边界数据")

    # 保留 admin_level、name 字段，区级要素作为覆盖分析单元
    keep = [c for c in ("name", "admin_level", "name:zh", "ref", "osm_id") if c in gdf.columns]
    gdf = gdf[[*keep, "geometry"]].dropna(subset=["name"])
    # 仅保留与上海相交的 admin_level=6 区（并可能保留市域用于底图）
    gdf = gdf[gdf["admin_level"].astype(str) == "6"].copy()

    if out_path:
        gdf.to_file(out_path, driver="GPKG")
    return gdf


def download_hospitals(out_path=None) -> gpd.GeoDataFrame:
    """下载上海医院 POI（amenity=hospital 或 healthcare=hospital）。

    注意：只查 node 且 out body（不加 geom）是最稳的查询组合，已验证可成功。
    若想补充 way 形式的医院边界，需在成功后再并入，避免量大时被 Overpass 拒绝。
    """
    query = f"""
    [out:json][timeout:{config.OVERPASS_TIMEOUT}];
    (
      node["amenity"="hospital"]({SHANGHAI_BBOX[1]},{SHANGHAI_BBOX[0]},{SHANGHAI_BBOX[3]},{SHANGHAI_BBOX[2]});
      node["healthcare"="hospital"]({SHANGHAI_BBOX[1]},{SHANGHAI_BBOX[0]},{SHANGHAI_BBOX[3]},{SHANGHAI_BBOX[2]});
    );
    out body;
    """
    data = _overpass(query)
    gdf = _elements_to_gdf(data.get("elements", []))
    gdf = _clip_to_bounds(gdf)
    if gdf.empty:
        raise RuntimeError("Overpass 未返回任何上海医院数据")

    # 优先用 name，否则用 address
    gdf["name"] = gdf.get("name", pd.Series(index=gdf.index, dtype=object))
    gdf["name"] = gdf["name"].fillna("未知医院")
    # 点化：way 边界取代表点（先转投影再取质心，保证精度）
    if "geometry" in gdf.columns:
        gdf = gdf.copy()
        pts = gdf.geometry.to_crs(config.CRS_UTM).centroid.to_crs(config.CRS_WGS84)
        gdf["geometry"] = pts

    keep = [c for c in ("name", "addr:street", "addr:housenumber", "addr:city", "osm_id") if c in gdf.columns]
    gdf = gdf[[*keep, "geometry"]].copy()
    # 合并 address 字段
    if "addr:street" in gdf.columns:
        gdf["address"] = gdf["addr:street"].fillna("") + gdf["addr:housenumber"].fillna("")
        gdf["address"] = gdf["address"].replace("", "地址未知")
        keep_addr = [c for c in keep if c not in ("addr:street", "addr:housenumber", "addr:city")]
        gdf = gdf[[*keep_addr, "address", "geometry"]]
    else:
        gdf["address"] = "地址未知"

    if out_path:
        gdf.to_file(out_path, driver="GPKG")
    return gdf


if __name__ == "__main__":
    try:
        districts = download_districts()
        print(f"行政区: {len(districts)} 个区")
        hospitals = download_hospitals()
        print(f"医院: {len(hospitals)} 家")
    except Exception as exc:  # noqa
        print(f"OSM 下载失败: {exc}")
    finally:
        time.sleep(0.1)