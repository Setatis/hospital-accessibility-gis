from __future__ import annotations

import numpy as np
import pandas as pd
from shapely.geometry import Point, Polygon, LineString, MultiPolygon
from shapely.ops import unary_union
import geopandas as gpd

import config

# 上海市 16 个行政区及其（近似）城区中心（经度, 纬度）
# used_e 为示意种子点，用于划分行政区归属；中心城区种子点对应的区。
DISTRICTS = [
    "黄浦区", "徐汇区", "长宁区", "静安区", "普陀区", "虹口区", "杨浦区",
    "闵行区", "宝山区", "嘉定区", "浦东新区", "金山区", "松江区", "青浦区",
    "奉贤区", "崇明区",
]
# 每个区的代表种子点（用于 Voronoi 式的近邻划分）
DISTRICT_SEEDS = {
    "黄浦区": (121.484, 31.232),
    "徐汇区": (121.437, 31.186),
    "长宁区": (121.416, 31.220),
    "静安区": (121.455, 31.227),
    "普陀区": (121.398, 31.250),
    "虹口区": (121.471, 31.260),
    "杨浦区": (121.522, 31.270),
    "闵行区": (121.381, 31.113),
    "宝山区": (121.410, 31.405),
    "嘉定区": (121.240, 31.380),
    "浦东新区": (121.600, 31.220),
    "金山区": (121.340, 30.750),
    "松江区": (121.228, 31.033),
    "青浦区": (121.110, 31.150),
    "奉贤区": (121.470, 30.918),
    "崇明区": (121.600, 31.620),
}

# 上海市（大陆+崇明）的粗略轮廓关键点，单位为经纬度
MAINLAND_PTS = [
    (120.900, 31.180), (120.850, 31.080), (120.950, 30.870), (121.100, 30.750),
    (121.300, 30.690), (121.500, 30.830), (121.650, 31.080), (121.680, 31.380),
    (121.550, 31.550), (121.300, 31.580), (121.100, 31.470), (121.000, 31.330),
]
CHONGMING_PTS = [
    (121.180, 31.520), (121.400, 31.660), (121.700, 31.700), (121.900, 31.580),
    (121.720, 31.430), (121.380, 31.420),
]


def _make_shanghai_outline() -> Polygon:
    """上海研究区近似多边形（大陆 + 崇明岛）。"""
    main_part = Polygon(MAINLAND_PTS)
    chongming = Polygon(CHONGMING_PTS)
    return unary_union([main_part, chongming])


def _assign_grid_cells(outline: Polygon, seed_map: dict, rows: int = 90, cols: int = 90):
    """将研究区网格单元按最近种子点归属到各行政区。

    返回 dict: 区名 -> shapely.Polygon（该区所有网格单元合并结果）
    """
    seed_names = list(seed_map.keys())
    seed_pts = np.array([seed_map[s] for s in seed_names])  # N x 2
    minx, miny, maxx, maxy = outline.bounds
    lons = np.linspace(minx, maxx, cols)
    lats = np.linspace(miny, maxy, rows)
    # 每个网格单元的中心点
    cells = []
    cent_lons, cent_lats = [], []
    for i in range(len(lons) - 1):
        for j in range(len(lats) - 1):
            c = (lons[i + 1] + lons[i]) / 2, (lats[j + 1] + lats[j]) / 2
            cent_lons.append(c[0]); cent_lats.append(c[1])
            cells.append(Polygon([
                (lons[i], lats[j]), (lons[i + 1], lats[j]),
                (lons[i + 1], lats[j + 1]), (lons[i], lats[j + 1]),
            ]))
    cents = np.column_stack([cent_lons, cent_lats])
    # 最近种子点
    d2 = ((cents[:, None, :] - seed_pts[None, :, :]) ** 2).sum(axis=2)  # n_cells x N
    assign = np.argmin(d2, axis=1)
    # 落入研究区且被裁剪的网格单元
    outline_shapely = outline
    membs: dict = {s: [] for s in seed_names}
    for ci, ai in enumerate(assign):
        name = seed_names[ai]
        g = cells[ci].intersection(outline_shapely)
        if g.is_empty:
            continue
        membs[name].append(g)
    # 合并各区几何
    geoms = {}
    for name, gs in membs.items():
        parts = [g for g in gs if not g.is_empty]
        if not parts:
            geoms[name] = None
        else:
            merged = unary_union(parts)
            if isinstance(merged, MultiPolygon):
                # 仅保留最大的若干块，避免碎片影响后续分析
                merged = max(parts, key=lambda g: g.area)
            geoms[name] = merged
    return geoms


def _make_hospitals(seed_map: dict, geoms: dict) -> gpd.GeoDataFrame:
    """按区种子点附近散布医院点，中心城区密度更高。"""
    rng = np.random.default_rng(2024)
    records = []
    center_districts = ["黄浦区", "静安区", "徐汇区", "长宁区", "虹口区", "杨浦区", "普陀区"]
    count = 0
    for name, seed in seed_map.items():
        n = 3 if name in center_districts else 2
        poly = geoms.get(name)
        if poly is None:
            continue
        placed = 0
        guard = 0
        while placed < n and guard < 200:
            guard += 1
            lon = seed[0] + rng.normal(0, 0.03)
            lat = seed[1] + rng.normal(0, 0.03)
            p = Point(lon, lat)
            if poly.contains(p):
                records.append({
                    "name": f"{name}演示医院{placed + 1}",
                    "address": f"{name}城区演示路{placed + 1}号",
                    "district": name,
                    "geometry": p,
                })
                placed += 1
    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=config.CRS_WGS84)
    return gdf


def _make_road_grid(outline: Polygon, spacing=0.007):
    """生成经纬网格状路网线（演示用）。spacing 越小路网越密，能支撑更细的步行等时圈。"""
    minx, miny, maxx, maxy = outline.bounds
    lines = []
    x = minx
    while x <= maxx:
        seg = LineString([(x, miny), (x, maxy)])
        if seg.intersects(outline):
            lines.append(seg)
        x += spacing
    y = miny
    while y <= maxy:
        seg = LineString([(minx, y), (maxx, y)])
        if seg.intersects(outline):
            lines.append(seg)
        y += spacing
    return gpd.GeoDataFrame({"geometry": lines}, geometry="geometry", crs=config.CRS_WGS84)


def generate_demo(output_dir: str | None = None) -> dict:
    """生成演示数据并写入 data/raw/，返回原始文件路径映射。

    Returns dict: {"districts": path, "hospitals": path, "road_network": path}
    """
    out_dir = config.RAW_DIR if output_dir is None else output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    outline = _make_shanghai_outline()
    geoms = _assign_grid_cells(outline, DISTRICT_SEEDS)

    # 1. 行政区
    dist_records = []
    for name in DISTRICT_SEEDS:
        g = geoms.get(name)
        if g is not None:
            dist_records.append({"name": name, "geometry": g})
    districts = gpd.GeoDataFrame(dist_records, geometry="geometry", crs=config.CRS_WGS84)
    districts_path = out_dir / "shanghai_districts_demo.gpkg"
    districts.to_file(districts_path, driver="GPKG")

    # 2. 医院
    hospitals = _make_hospitals(DISTRICT_SEEDS, geoms)
    hospitals_path = out_dir / "hospitals_demo.gpkg"
    hospitals.to_file(hospitals_path, driver="GPKG")

    # 3. 路网
    roads = _make_road_grid(outline)
    roads_path = out_dir / "road_network_demo.gpkg"
    roads.to_file(roads_path, driver="GPKG")

    return {
        "districts": str(districts_path),
        "hospitals": str(hospitals_path),
        "road_network": str(roads_path),
        "districts_gdf": districts,
        "hospitals_gdf": hospitals,
        "road_network_gdf": roads,
    }


if __name__ == "__main__":
    res = generate_demo()
    print("演示数据已生成:")
    for k, v in res.items():
        print(f"  {k}: {v}")