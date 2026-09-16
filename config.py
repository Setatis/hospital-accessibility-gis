from pathlib import Path

# ------------------------------------------------------------------
# 目录结构
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"                 # 原始下载/生成数据
INTERMEDIATE_DIR = DATA_DIR / "intermediate"
PROCESSED_DIR = DATA_DIR / "processed"

OUTPUT_DIR = BASE_DIR / "output"
MAP_DIR = OUTPUT_DIR / "maps"              # 静态 PNG
STATS_DIR = OUTPUT_DIR / "stats"           # CSV / GeoJSON

# 运行期创建缺失目录
for _d in (RAW_DIR, INTERMEDIATE_DIR, PROCESSED_DIR, MAP_DIR, STATS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------
# 研究区（上海市）
# ------------------------------------------------------------------
SHANGHAI_CENTER = (31.2304, 121.4737)      # (经度, 纬度) WGS84 市中心

# ------------------------------------------------------------------
# 坐标系
# ------------------------------------------------------------------
CRS_WGS84 = "EPSG:4326"                     # 地理坐标（度）
CRS_UTM = "EPSG:32651"                      # UTM 51N，覆盖上海，适用于面积计算
CRS_GK = "EPSG:21463"                       # 上海高斯-克吕格（传统地方坐标系，可选）

# ------------------------------------------------------------------
# 覆盖分析参数
# ------------------------------------------------------------------
TRAVEL_TIME_MIN = 10                        # 覆盖时间阈值（分钟）

# 驾车：上海城区限速一般 40km/h，10min => 约 6.67km
DRIVE_SPEED_KMH = 40.0
DRIVE_RADIUS_M = TRAVEL_TIME_MIN / 60.0 * DRIVE_SPEED_KMH * 1000

# 步行：平均约 4.8km/h，10min => 约 0.8km
WALK_SPEED_KMH = 4.8
WALK_RADIUS_M = TRAVEL_TIME_MIN / 60.0 * WALK_SPEED_KMH * 1000

# 分析方案与出行方式
BUFFER_METHOD = "buffer"                    # 直线缓冲区
NETWORK_METHOD = "network"                  # 路网等时圈
METHODS = [BUFFER_METHOD, NETWORK_METHOD]
MODES = ["drive", "walk"]

# 路网分析参数（osmnx）
NETWORK_TYPE = "drive"                      # drive/walk/all_service
DRIVE_NETWORK_MAX_SPEED = DRIVE_SPEED_KMH   # 若无 speed 限值时的兜底速度
WALK_NETWORK_SPEED = WALK_SPEED_KMH

# ------------------------------------------------------------------
# 栅格分析参数
# ------------------------------------------------------------------
RASTER_RES_M = 500                          # 栅格分辨率（米）
RASTER_FILL = -9999.0

# ------------------------------------------------------------------
# 数据获取参数
# ------------------------------------------------------------------
# Overpass API 实例列表：主实例 + 备用镜像（公共服务可能负载高/超时，按序尝试）
OVERPASS_API = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
OVERPASS_TIMEOUT = 180
FETCH_TIMEOUT_S = 300
# Overpass 公共服务负载波动大，每个实例按序重试次数
OVERPASS_RETRIES = 2

# 可选 HTTPS/HTTP 代理（用于 OSM/osmnx 下载；置空字符串则直连）。
# 注意：阿里 DataV（国内）不需要代理，本项目只在 OSM/osmnx 下载时注入该代理。
PROXY_URL = "http://127.0.0.1:7890"  # 例如 "http://127.0.0.1:7890"

# 阿里 DataV 行政区划 GeoJSON（国内直连稳定，权威区划数据；310000 = 上海市）
DATAV_AREA_URL = "https://geo.datav.aliyun.com/areas_v3/bound/310000_full.json"

# OSM 路网缓存（osmnx 下载一次后落盘复用，避免每次依赖 Overpass 即时可用）
ROAD_NETWORK_CACHE = DATA_DIR / "raw" / "road_network_online.graphml"

# Geofabrik 本地 pbf 与提取的上海路网缓存（稳定替代 Overpass 的真实路网方案）
SHANGHAI_PBF = DATA_DIR / "raw" / "china-latest.osm.pbf"
SHANGHAI_ROADS_GPKG = DATA_DIR / "raw" / "shanghai_roads_geofabrik.gpkg"

# ------------------------------------------------------------------
# 网络 / 演示数据下载超时
# ------------------------------------------------------------------
NETWORK_FETCH_TIMEOUT_S = 120