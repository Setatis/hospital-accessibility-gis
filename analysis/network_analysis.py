"""路网网络分析（等时圈）。

通过真实道路网络计算各医院在设定时间（10 分钟）内可达的覆盖范围，
相比直线缓冲区更符合实际交通可达性。

两种建图方式：
1. build_graph_from_roads(roads_gdf) —— 从任意道路线要素构建平面路网图（离线可用，
   适用于演示路网或用户提供的路网数据）。
2. build_graph_osmnx(area_polygon, mode) —— 通过 osmnx 下载 OSM 真实路网构建（联网）。

等时圈：在网络图上以医院最近节点为源，用 Dijkstra 按边权重（UTM 米）计算 cutoff 内
可达的边，合并其线几何后做缓冲得到网络形状的覆盖多边形。
"""
from __future__ import annotations

import logging

import geopandas as gpd
import networkx as nx
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import LineString, MultiLineString, MultiPoint
from shapely.ops import unary_union
from shapely.geometry import Point

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 平面路网建图
# ---------------------------------------------------------------------------
def _line_to_nodes(line, node_map):
    """把一条线段写入 node_map，返回边列表 [(u, v, length)]。"""
    coords = list(line.coords)
    edges = []
    for i in range(len(coords) - 1):
        a = coords[i]
        b = coords[i + 1]
        u = node_map.setdefault(a, len(node_map))
        v = node_map.setdefault(b, len(node_map))
        if u != v:
            length = np.hypot(b[0] - a[0], b[1] - a[1])
            edges.append((u, v, length, LineString([a, b])))
    return edges


def build_graph_from_roads(roads_gdf: gpd.GeoDataFrame, target_crs: str = config.CRS_UTM) -> nx.Graph | None:
    """从道路线要素构建平面（planar）路网图。

    先对全部线做 unary_union，让相交处自动打断，再逐段建边，保证交叉口连通。
    """
    roads = roads_gdf.to_crs(target_crs)
    lines = [g for g in roads.geometry if (isinstance(g, LineString) and not g.is_empty)]
    if not lines:
        return None
    merged = unary_union(lines)  # planarly noded linework
    parts = list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]
    parts = [p for p in parts if isinstance(p, LineString)]

    G = nx.Graph()
    node_map = {}
    for part in parts:
        for u, v, length, geom in _line_to_nodes(part, node_map):
            if G.has_edge(u, v):
                continue
            G.add_edge(u, v, length=length, geom=geom)

    # 存节点坐标
    pos = {nid: np.array(coord) for coord, nid in node_map.items()}
    nx.set_node_attributes(G, pos, "pos")
    if G.number_of_nodes() == 0:
        return None
    return G


def build_graph_osmnx(area_gdf: gpd.GeoDataFrame, mode: str) -> nx.Graph | None:
    """用 osmnx 下载研究区 OSM 路网并转为无向平面图（边带 length，单位米）。

    优先复用已下载的路网缓存（config.ROAD_NETWORK_CACHE）；无缓存时在线下载并落盘。
    代理策略：
      - 启用 config.PROXY_URL 时，临时注入系统代理环境变量供 osmnx/requests 使用，
        并发往 overpass-api.de（实测走代理时该镜像稳定）；
      - 否则直连并发往 kumi 镜像（实测直连时 kumi 稳定）。
    """
    import osmnx as ox

    cache_path = config.ROAD_NETWORK_CACHE
    if cache_path and cache_path.exists():
        G = ox.load_graphml(cache_path)
        logger.info("复用路网缓存: %s", cache_path)
        return _to_undirected_utm(G, ox)

    ox.settings.use_cache = False
    import os
    saved_env = {}
    use_proxy = bool(config.PROXY_URL)
    try:
        if use_proxy:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                saved_env[key] = os.environ.get(key)
                os.environ[key] = config.PROXY_URL
            # 走代理时用 osmnx 默认镜像（overpass-api.de），实测可用
            ox.settings.overpass_url = "https://overpass-api.de"
        elif config.OVERPASS_API:
            # 直连时用 kumi。注意：osmnx 会在 overpass_url 后自动拼接 "/interpreter"，
            # 这里只提供 base URL。
            ox.settings.overpass_url = config.OVERPASS_API[0].rsplit("/interpreter", 1)[0]

        try:
            polygon = area_gdf.to_crs(config.CRS_WGS84).union_all()
            network_type = "drive" if mode == "drive" else "walk"
            G = ox.graph_from_polygon(polygon, network_type=network_type, simplify=True)
        except Exception as exc:  # noqa
            # 直连失败时若已启用代理，回退尝试代理镜像
            if not use_proxy and config.PROXY_URL:
                for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                    os.environ[key] = config.PROXY_URL
                logger.warning("直连路网下载失败(%s)，回退走代理重试…", exc)
                ox.settings.overpass_url = "https://overpass-api.de"
                polygon = area_gdf.to_crs(config.CRS_WGS84).union_all()
                network_type = "drive" if mode == "drive" else "walk"
                G = ox.graph_from_polygon(polygon, network_type=network_type, simplify=True)
            else:
                raise

        if G is None or G.number_of_nodes() == 0:
            return None
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            ox.save_graphml(G, filepath=cache_path)
        return _to_undirected_utm(G, ox)
    except Exception as exc:  # noqa
        logger.warning("osmnx 下载路网失败: %s", exc)
        return None
    finally:
        if use_proxy or saved_env:
            for key, val in saved_env.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val


def _to_undirected_utm(G, ox) -> nx.Graph:
    """转为无向图，统一到 UTM 米，边含 length 权重。"""
    Gu = nx.Graph(ox.utils_graph.get_undirected(G))
    Gu = ox.projection.project_graph(Gu, to_crs=config.CRS_UTM)
    return Gu


# ---------------------------------------------------------------------------
# 等时圈计算
# ---------------------------------------------------------------------------
def _build_node_index(G: nx.Graph):
    """构建图节点的坐标数组与 cKDTree 索引，用于快速最近点查询。

    Returns:
        (node_ids, coords, tree)：node_ids 与 coords 顺序一致；tree 对 coords 建索引。
    """
    pos = nx.get_node_attributes(G, "pos")
    node_ids = list(pos.keys())
    coords = np.asarray([pos[n] for n in node_ids], dtype=float)
    tree = cKDTree(coords)
    return node_ids, coords, tree


def _nearest_node(tree: cKDTree, coords: np.ndarray, node_ids: list, point: Point):
    """基于 cKDTree 返回距离 point 最近的图节点 id。"""
    if tree.n == 0:
        return None
    d, idx = tree.query([point.x, point.y])
    return node_ids[idx]


def _isochrone_polygon(G: nx.Graph, point: Point, radius_m: float, buffer_m: float,
                       tree=None, node_ids=None, coords=None):
    """在网络图中，返回从 point 出发在 radius_m 内的覆盖多边形。

    通过对可达边线几何做缓冲区合并形成网络形状的等时覆盖。
    point 应为与图节点一致的投影坐标（UTM）；tree/node_ids/coords 为预构建索引。
    """
    source = _nearest_node(tree, coords, node_ids, point)
    if source is None:
        return None
    # 到源距离词典（仅保留 cutoff 内）
    dist = nx.single_source_dijkstra_path_length(G, source, cutoff=radius_m, weight="length")
    if not dist:
        return None
    reachable = set(dist.keys())
    pos = nx.get_node_attributes(G, "pos")
    # 只扫描可达节点自身及其邻边（避免遍历全图），收集两端均可达且带几何的边
    line_geoms = []
    seen = set()
    for u in reachable:
        for v, attrs in G[u].items():
            key = (u, v) if u <= v else (v, u)
            if key in seen:
                continue
            seen.add(key)
            if v in reachable and "geom" in attrs:
                line_geoms.append(attrs["geom"])

    # 等时圈形状：当可达街道数量少时用精确的 buffered union（保留路线形状）；
    # 街道众多时对真实路网求 union 后 buffer 会因内环/岛过多而极慢，退化为
    # 可达节点的凸包近似（对医院宏观覆盖足够，且毫秒级出结果）。
    EDGE_PRECISE_LIMIT = 400
    if len(line_geoms) <= EDGE_PRECISE_LIMIT and line_geoms:
        poly = unary_union(line_geoms).buffer(buffer_m)
    else:
        pts = MultiPoint([Point(*pos[n]) for n in reachable])
        hull = pts.convex_hull
        if hull.is_empty:
            return None
        poly = hull.buffer(buffer_m)
    if poly.is_empty:
        return None
    poly = poly.buffer(0)  # fix invalid
    return poly


def compute_network_coverage(
    hospitals: gpd.GeoDataFrame,
    districts: gpd.GeoDataFrame,
    mode: str,
    roads_gdf: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """为每家医院计算 10 分钟路网可达覆盖，并裁剪到其所属行政区。

    Returns:
        GeoDataFrame: hospital_id, name, district, mode, coverage geometry（UTM）
    """
    radius = config.DRIVE_RADIUS_M if mode == "drive" else config.WALK_RADIUS_M
    buffer_m = 180 if mode == "drive" else 90

    if roads_gdf is not None:
        G = build_graph_from_roads(roads_gdf)
    else:
        G = build_graph_osmnx(districts, mode)

    if G is None:
        logger.warning("未能构建路网图，%s 方案返回空覆盖", mode)
        return gpd.GeoDataFrame(columns=["hospital_id", "name", "district", "mode", "geometry", "area"],
                                crs=districts.crs)

    # 图节点坐标在 UTM（build_graph_from_roads/osmnx 均已投影），预建最近点索引；
    # 医院点需投影到同一坐标系再查询。
    node_ids, coords, tree = _build_node_index(G)
    graph_crs = config.CRS_UTM  # 图始终投影到 UTM 米
    hosp_geom = hospitals.geometry.to_crs(graph_crs) if hospitals.crs != graph_crs else hospitals.geometry

    rows = []
    failed = 0
    for i, (_, row) in enumerate(hospitals.iterrows()):
        point = hosp_geom.iloc[i]
        try:
            poly = _isochrone_polygon(G, point, radius, buffer_m, tree, node_ids, coords)
        except Exception as exc:  # noqa
            logger.warning("医院 %s 网路分析失败: %s", row.get("name"), exc)
            poly = None
        if poly is None:
            failed += 1
            rows.append({
                "hospital_id": row.get("hospital_id"),
                "name": row.get("name"),
                "district": row.get("district"),
                "mode": mode,
                "geometry": None,
            })
            continue
        # 若该医院有归属行政区，仅保留区内的有效覆盖
        dgeom = districts.loc[districts["name"] == row.get("district"), "geometry"]
        eff = poly if dgeom.empty else poly.intersection(dgeom.iloc[0])
        rows.append({
            "hospital_id": row.get("hospital_id"),
            "name": row.get("name"),
            "district": row.get("district"),
            "mode": mode,
            "geometry": None if eff.is_empty else eff,
        })

    if failed:
        logger.info("%s: %d 家医院无可达网络，记为无覆盖", mode, failed)

    result = gpd.GeoDataFrame(rows, crs=districts.crs)
    # 修复求交后可能的无效/退化几何，再计算面积
    result["geometry"] = result.geometry.apply(
        lambda g: g.buffer(0) if g is not None and not g.is_empty and g.is_valid is False else g)
    keep = result.geometry.notna() & ~result.geometry.is_empty & result.geometry.apply(
        lambda g: (g.area if g is not None else 0) > 0)
    result = result[keep].copy()
    result["area"] = result.geometry.to_crs(config.CRS_UTM).area
    return result