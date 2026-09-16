# 上海市医院 10 分钟可达覆盖 GIS 分析

基于 **Python 纯开源 GIS 技术栈**（geopandas + rasterio + osmnx + networkx + matplotlib）构建的上海市医院 10 分钟可达覆盖分析项目。

## 功能特性

1. **数据获取与预处理**
   - 从 OpenStreetMap (Overpass API) 获取上海市行政区边界与医院 POI，自动重投影、清洗、空间连接
   - 网络不可用 / 下载失败时**自动回退到内置演示数据**，保证流程离线可运行

2. **空间分析**
   - 直线缓冲区（驾车 40km/h → 10min≈6.7km；步行 4.8km/h → 10min≈0.8km）
   - 路网网络等时圈（基于真实/演示路网的最短路径可达性，更符合实际交通）

3. **统计分析**
   - 每个行政区的**总面积、覆盖面积、覆盖率百分比、区内医院数**统计表（CSV / GeoJSON）
   - rasterio 栅格化交叉验证（500m 栅格分区统计）

4. **可视化**
   - 静态专题图（matplotlib，PNG）：
     - 医院分布 + 10 分钟覆盖范围图
     - 行政区覆盖率专题图（缓冲区/路网 × 驾车/步行）

## 目录结构

```
apply/
├── main.py                       # 主入口
├── config.py                     # 全局配置（路径/车速/CRS/参数）
├── requirements.txt
├── analysis/
│   ├── fetch_data.py             # OSM Overpass 数据下载
│   ├── generate_demo_data.py     # 离线演示数据合成
│   ├── preprocess.py             # 清洗/投影/空间连接
│   ├── buffer_analysis.py        # 直线缓冲区覆盖
│   ├── network_analysis.py       # 路网网络等时圈
│   ├── coverage_stats.py         # 覆盖率统计
│   └── raster_analysis.py        # rasterio 栅格分析
├── visualization/
│   └── static_maps.py            # 静态地图
├── data/                         # 运行时生成（raw/intermediate/processed）
└── output/                       # 结果输出（maps/stats）
```

## 环境安装

```bash
python -m pip install -r requirements.txt
```

主要依赖：geopandas、rasterio、osmnx、networkx、matplotlib、shapely、pyproj、requests、pandas、numpy、pyogrio、scipy。

## 使用方法

```bash
# 方式一：默认（先尝试 OSM 真实数据，失败自动回退演示数据）
python main.py

# 方式二：强制离线演示数据（无网络环境，结果可复现）
python main.py --use-demo
```

## 输出结果

运行完成后会打印所有文件路径：

| 类型 | 位置 | 说明 |
|---|---|---|
| 统计表 | `output/stats/coverage_stats.csv` | 4 种组合（缓冲区/路网 × 驾车/步行）各区覆盖率 |
| 统计表 | `output/stats/coverage_raster_stats.csv` | 栅格交叉验证统计 |
| 地理数据 | `output/stats/districts_coverage.geojson` | 带覆盖率属性的行政区 |
| 静态地图 | `output/maps/*.png` | 医院分布+覆盖图、覆盖率专题图 |

## 技术说明

- 覆盖范围裁剪到各自行政区边界内，避免跨区覆盖重复计算。
- 覆盖率 = 区内各医院覆盖范围并集面积 / 区总面积 × 100%。
- 路网等时圈：将医院吸附到最近路网节点，Dijkstra 计算 10 分钟内可达路段，合并后缓冲为覆盖多边形。
