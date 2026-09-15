"""上海医院 10 分钟可达覆盖 GIS 分析 —— 主入口。

编排流程：数据准备 → 预处理 → 双方案空间分析 → 统计 → 栅格分析 → 可视化。

运行方式：
    python main.py [--use-demo]

参数：
    --use-demo   强制使用离线演示数据（不尝试 OSM 下载），适合无网络环境。

数据策略：
    默认先尝试从 Overpass 下载真实 OSM 数据；任一下载失败即回退到演示数据，
    确保流程始终可运行。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import geopandas as gpd

# 确保项目模块可导入
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from analysis import fetch_data, generate_demo_data, preprocess
from analysis import buffer_analysis, network_analysis
from analysis import coverage_stats, raster_analysis
from visualization import static_maps

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def prepare_data(force_demo: bool = False) -> dict:
    """数据准备：优先复用已缓存的在线数据，其次在线下载，失败回退演示。返回原始数据 dict。

    roads_raw：优先使用本地 Geofabrik 提取的真实路网（config.SHANGHAI_ROADS_GPKG /
    config.SHANGHAI_PBF），稳定不依赖 Overpass；否则回退演示路网（在线场景下网络方案
    才会用到，离线演示仍用合成路网）。
    """
    raw_dir = config.RAW_DIR
    districts_raw = hospitals_raw = roads_raw = None
    used_demo = False
    districts_cache = raw_dir / "shanghai_districts_online.gpkg"
    hospitals_cache = raw_dir / "hospitals_online.gpkg"

    if not force_demo:
        # ① 优先复用已成功下载的缓存（Overpass 公共服务不稳定，避免重复等待）
        if districts_cache.exists() and hospitals_cache.exists():
            try:
                districts_raw = gpd.read_file(districts_cache)
                hospitals_raw = gpd.read_file(hospitals_cache)
                if districts_raw.empty or hospitals_raw.empty:
                    raise RuntimeError("缓存文件为空")
                logger.info("复用在线数据缓存：%d 个行政区、%d 家医院",
                            len(districts_raw), len(hospitals_raw))
            except Exception as exc:  # noqa
                logger.warning("缓存数据读取失败，重新下载。原因: %s", exc)
                districts_raw = hospitals_raw = None

        # ② 无缓存时在线下载
        if districts_raw is None or hospitals_raw is None:
            try:
                logger.info("尝试下载上海市行政区(DataV)与医院(OSM)数据...")
                districts_raw = fetch_data.download_districts(districts_cache)
                hospitals_raw = fetch_data.download_hospitals(hospitals_cache)
                logger.info("在线数据下载成功：%d 个行政区、%d 家医院",
                            len(districts_raw), len(hospitals_raw))
            except Exception as exc:  # noqa
                logger.warning("在线数据下载失败，回退到演示数据。原因: %s", exc)
                districts_raw = hospitals_raw = None

    # ③ 尝试真实路网（本地 pbf / 已提取缓存），失败则保持 None（回退演示路网）
    if not force_demo:
        try:
            from analysis import road_network_geofabrik
            roads_raw = road_network_geofabrik.extract_shanghai_roads()
            logger.info("真实路网就绪：%d 条线", len(roads_raw))
        except Exception as exc:  # noqa
            logger.warning("真实路网不可用，将回退演示路网。原因: %s", exc)
            roads_raw = None

    if districts_raw is None or hospitals_raw is None:
        logger.info("使用离线演示数据...")
        demo = generate_demo_data.generate_demo(raw_dir)
        districts_raw = demo["districts_gdf"]
        hospitals_raw = demo["hospitals_gdf"]
        if roads_raw is None:
            roads_raw = demo["road_network_gdf"]
        used_demo = True
        logger.info("演示数据就绪：%d 个行政区、%d 家医院", len(districts_raw), len(hospitals_raw))

    return {"districts": districts_raw, "hospitals": hospitals_raw, "roads": roads_raw,
            "used_demo": used_demo}


def main(force_demo: bool = False) -> int:
    logger.info("==== 上海医院 10 分钟可达覆盖 GIS 分析 ====")

    # 1. 数据准备
    raw = prepare_data(force_demo)
    data_source = ("演示数据(离线)" if raw["used_demo"]
                   else "在线数据(DataV 行政区 + OSM 医院)")

    # 2. 预处理
    logger.info("预处理：清洗 + 投影 + 空间连接...")
    prepped = preprocess.prepare(raw["districts"], raw["hospitals"])
    districts = prepped["districts"]
    hospitals = prepped["hospitals"]
    logger.info("行政区 %d 个，医院 %d 家", len(districts), len(hospitals))

    # 3. 空间分析：双方案 × 双出行方式
    results = {}          # {(method, mode): coverage_gdf}
    for mode in config.MODES:
        for method in config.METHODS:
            logger.info("分析：%s · %s", method, mode)
            if method == config.BUFFER_METHOD:
                cov = buffer_analysis.run(hospitals, districts, mode)
            else:
                cov = network_analysis.compute_network_coverage(
                    hospitals, districts, mode, roads_gdf=raw["roads"]
                )
            results[(method, mode)] = cov
            logger.info("  生成 %d 个有效覆盖范围", len(cov))

    # 4. 覆盖率统计
    logger.info("计算行政区覆盖率统计表...")
    stats_table = coverage_stats.build_stats_table(results, districts)
    exports = coverage_stats.export(
        stats_table, districts, config.STATS_DIR
    )
    logger.info("统计表已导出：%s", exports["csv"])

    # 5. 栅格分析（交叉验证 + 演示 rasterio）
    logger.info("栅格分区统计（rasterio）...")
    raster_table = raster_analysis.run_all(results, districts, config.PROCESSED_DIR)
    if not raster_table.empty:
        rall_csv = config.STATS_DIR / "coverage_raster_stats.csv"
        raster_table.to_csv(rall_csv, index=False, encoding="utf-8-sig")
        logger.info("栅格统计表已导出：%s", rall_csv)

    # 6. 可视化
    logger.info("生成静态地图...")
    map_paths = static_maps.render_all(districts, hospitals, results, stats_table, config.MAP_DIR)

    # ---- 结果汇总 ----
    sep = "-" * 60
    print("\n" + sep)
    print("✅ 分析完成。数据源:", data_source)
    print(sep)
    print("【统计表】")
    print("  CSV      :", exports["csv"])
    print("  GeoJSON  :", exports["geojson"])
    print("  栅格统计 :", config.STATS_DIR / "coverage_raster_stats.csv")
    print("\n【静态地图】")
    for p in map_paths:
        print("  -", p)
    print("\n【覆盖率速览】(buffer-drive) 覆盖率最高的5个区:")
    sub = stats_table[(stats_table["method"] == "buffer") & (stats_table["mode"] == "drive")]
    if not sub.empty:
        top = sub.sort_values("coverage_pct", ascending=False).head(5)
        for _, r in top.iterrows():
            print(f"    {r['district']}: {r['coverage_pct']}%")
    print(sep)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="上海医院 10 分钟可达覆盖分析")
    parser.add_argument("--use-demo", action="store_true", help="强制使用离线演示数据")
    args = parser.parse_args()
    raise SystemExit(main(force_demo=args.use_demo))