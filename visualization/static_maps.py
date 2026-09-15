"""静态地图可视化（matplotlib）。

生成两类高质量静态图：
1. hospital_map: 医院分布 + 双方案 10 分钟覆盖范围叠加（驾车/步行分图）
2. choropleth_map: 行政区医院覆盖率专题图（按覆盖率分级着色）

需传入覆盖结果与统计表；几何统一用黄浦区为中心的投影方便展示。
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import geopandas as gpd
import numpy as np

import config

# 设置中文字体，避免中文显示为方块
import matplotlib.font_manager as fm
_available = {f.name for f in fm.fontManager.ttflist}
for _font in ("Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC", "Arial Unicode MS"):
    if _font in _available:
        plt.rcParams["font.sans-serif"] = [_font, "DejaVu Sans"]
        break
plt.rcParams["axes.unicode_minus"] = False


def _to_display(gdf: gpd.GeoDataFrame, crs: str = config.CRS_UTM) -> gpd.GeoDataFrame:
    """将结果转为展示坐标系（默认 UTM，便于欧氏缓冲展示）。"""
    return gdf.to_crs(crs) if gdf.crs is not None else gdf


def hospital_map(districts, hospitals, coverage_gdfs, mode: str, out_path: str):
    """医院分布 + 某出行方式下 两方案覆盖范围叠加。"""
    districts_d = _to_display(districts)
    hospitals_d = _to_display(hospitals)
    fig, axes = plt.subplots(1, 2, figsize=(16, 9), facecolor="white")
    methods = ["buffer", "network"]
    method_labels = {"buffer": "直线缓冲区", "network": "路网等时圈"}
    colors = {"buffer": "#e74c3c", "network": "#2980b9"}
    alpha = {"buffer": 0.35, "network": 0.45}

    for ax, method in zip(axes, methods):
        key = (method, mode)
        if key not in coverage_gdfs:
            ax.set_title(f"{method_labels[method]} - 无数据")
            ax.set_xticks([]); ax.set_yticks([])
            continue
        cov = _to_display(coverage_gdfs[key])
        districts_d.plot(ax=ax, color="#efefef", edgecolor="#888888", linewidth=1.0)
        cov.plot(
            ax=ax, color=colors[method], alpha=alpha[method], edgecolor="none",
            legend=False,
        )
        hospitals_d.plot(ax=ax, color="#111111", markersize=22, marker="x", zorder=5)
        for _, h in hospitals_d.head(15).iterrows():
            ax.annotate(
                h["name"], (h.geometry.x, h.geometry.y),
                fontsize=6, xytext=(3, 3), textcoords="offset points", color="#333333",
            )
        ax.set_title(f"{method_labels[method]} · 10分钟覆盖（{mode}）", fontsize=13)
        ax.set_axis_off()
    # 图例
    handles = [
        Patch(facecolor="#e74c3c", alpha=0.35, label="直线缓冲区覆盖"),
        Patch(facecolor="#2980b9", alpha=0.45, label="路网等时圈覆盖"),
        Patch(facecolor="white", label=""),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=2, frameon=False)
    fig.suptitle(f"上海市医院分布与 10 分钟可达覆盖范围（{mode}）", fontsize=16, y=0.98)
    plt.tight_layout(rect=[0, 0.05, 1, 0.95])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def choropleth_map(districts, stats_table, mode: str, out_path: str, method: str = "buffer"):
    """行政区覆盖率专题图（choropleth）。按 coverage_pct 分级着色。"""
    districts_d = _to_display(districts).copy()
    districts_d["district"] = districts_d["name"]
    sub = stats_table[(stats_table["mode"] == mode) & (stats_table["method"] == method)]
    merged = districts_d.merge(sub, how="left", on="district")
    merged["coverage_pct"] = merged["coverage_pct"].fillna(0.0)

    # 用于地图显示的覆盖率（绿-黄-红渐变，越高越好）
    cmap = plt.get_cmap("RdYlGn")
    norm = plt.Normalize(vmin=0, vmax=100)
    fig, ax = plt.subplots(figsize=(12, 10), facecolor="white")
    merged.plot(
        ax=ax, column="coverage_pct", cmap=cmap, norm=norm,
        edgecolor="black", linewidth=0.8,
        legend=True, legend_kwds={"label": "覆盖率 (%)", "shrink": 0.7, "orientation": "vertical"},
    )
    ax.set_title(f"上海市各区医院 10 分钟覆盖率专题图 · {mode}", fontsize=15)
    ax.set_axis_off()
    # 标注覆盖率
    for _, r in merged.iterrows():
        centroid = r.geometry.centroid
        ax.annotate(
            f"{r['district']}\n{r['coverage_pct']:.1f}%",
            (centroid.x, centroid.y), fontsize=7, ha="center", va="center",
            color="#000000",
        )
    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_all(districts, hospitals, coverage_gdfs, stats_table, out_dir=None) -> list:
    """生成全部静态图，返回输出文件路径列表。"""
    out_dir = config.MAP_DIR if out_dir is None else out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for mode in config.MODES:
        p_img = out_dir / f"hospitals_cover_{mode}.png"
        hospital_map(districts, hospitals, coverage_gdfs, mode, p_img)
        paths.append(str(p_img))
        for method in config.METHODS:
            p_chor = out_dir / f"choropleth_{method}_{mode}.png"
            choropleth_map(districts, stats_table, mode, p_chor, method)
            paths.append(str(p_chor))
    return paths