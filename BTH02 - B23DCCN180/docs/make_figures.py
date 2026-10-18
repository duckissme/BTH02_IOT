"""Sinh hình cho báo cáo từ dữ liệu giả lập (python docs/make_figures.py)."""
import json, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from src.preprocess_core import preprocess
from src.metrics import latency_summary, loss_summary
plt.rcParams["font.family"] = "DejaVu Sans"
F = ROOT / "docs" / "figures"

# ---- 1. kiến trúc ----
fig, ax = plt.subplots(figsize=(11, 4.2)); ax.axis("off"); ax.set_xlim(0, 112); ax.set_ylim(-1, 38)
def box(x, y, w, h, t, c):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2", fc=c, ec="#333", lw=1))
    ax.text(x + w / 2, y + h / 2, t, ha="center", va="center", fontsize=8)
def arrow(x1, y1, x2, y2, t="", dy=1.8, style="->", dx=0):
    ax.annotate("", (x2, y2), (x1, y1), arrowprops=dict(arrowstyle=style, lw=1.3))
    if t: ax.text((x1 + x2) / 2 + dx, (y1 + y2) / 2 + dy, t, ha="center", fontsize=7.2, color="#444")
y0, h = 16, 11
box(1, y0, 17, h, "Cảm biến\nDHT22 + HC-SR04", "#fde9c9")
box(24, y0, 16, h, "ESP32\n(Wokwi / thật)\nNTP + JSON", "#d9ead3")
box(46, y0, 17, h, "MQTT Broker\nEMQX / Mosquitto", "#cfe2f3")
box(69, y0, 19, h, "Collector (Python)\nvalidate · dedupe\nđếm mất gói", "#d9ead3")
box(94, y0, 17, h, "InfluxDB 2.x\niot_raw (30 ngày)\niot_clean (365 ngày)", "#ead1dc")
box(58, 0, 26, 8, "Preprocess (pandas)\nclean · outlier · resample\nrolling · chuẩn hóa", "#d9ead3")
box(94, 0, 17, 8, "Grafana / Streamlit\nreal-time + đã xử lý", "#cfe2f3")
mid = y0 + h / 2
arrow(18.5, mid, 23.5, mid, "GPIO"); arrow(40.5, mid, 45.5, mid, "publish")
arrow(63.5, mid, 68.5, mid, "subscribe"); arrow(88.5, mid, 93.5, mid, "write")
arrow(99, y0 - .5, 99, 8.7, "", style="->"); ax.text(102.5, 12, "query", fontsize=7.2, color="#444")
arrow(96, y0 - .5, 84.7, 6.5, "", style="<->"); ax.text(87.5, 13.5, "đọc raw /\nghi clean", fontsize=7.2, color="#444", ha="center")
ax.text(43, 31, "topic: iot/b23dccn180/esp32-01/telemetry", fontsize=8.5, ha="center", style="italic")
ax.plot([43, 43], [29.6, 27.6], ls=":", color="#555", lw=1)
fig.savefig(F / "architecture.png", dpi=200, bbox_inches="tight"); plt.close(fig)

# ---- 2. trước/sau tiền xử lý ----
raw = pd.read_csv(ROOT / "data/sim_raw.csv", index_col=0, parse_dates=True)
res = {}
for m in ("iqr", "hampel"):
    res[m] = preprocess(raw, "30s", method=m)
clean, rep = res["hampel"]
fig, axes = plt.subplots(3, 1, figsize=(10, 7), sharex=True)
for ax_, c, u in zip(axes, ["temperature", "humidity", "distance_cm"], ["°C", "%RH", "cm"]):
    ax_.plot(raw.index, raw[c], ".", ms=2.5, color="#999", label="thô")
    ax_.plot(clean.index, clean[c], "-", lw=1.4, color="#1f77b4", label="resample 30 s + nội suy")
    ax_.plot(clean.index, clean[f"{c}_rollmean"], "-", lw=1.2, color="#d62728", label="rolling mean (5)")
    gap = clean[clean["is_gap"]]
    for t in gap.index: ax_.axvspan(t, t + pd.Timedelta("30s"), color="#ffcc00", alpha=.35)
    ax_.set_ylabel(f"{c} ({u})"); ax_.grid(alpha=.3)
axes[0].legend(ncol=3, fontsize=8, loc="upper right"); axes[0].set_title("Trước và sau tiền xử lý (dữ liệu giả lập 2 giờ; vùng vàng = khoảng trống không nội suy)", fontsize=10)
fig.tight_layout(); fig.savefig(F / "preprocess.png", dpi=170); plt.close(fig)

# ---- 3. phân bố độ trễ ----
fig, ax = plt.subplots(figsize=(6, 2.8))
ax.hist(raw["latency_ms"], bins=60, color="#6fa8dc"); ax.set_xlabel("latency_ms"); ax.set_ylabel("số bản tin")
l = latency_summary(raw); ax.axvline(l["p95"], color="r", ls="--", label=f"P95 = {l['p95']:.0f} ms"); ax.legend()
fig.tight_layout(); fig.savefig(F / "latency_hist.png", dpi=170); plt.close(fig)

out = {"iqr": res["iqr"][1], "hampel": res["hampel"][1], "latency": l, "loss": loss_summary(raw)}
(ROOT / "docs" / "sim_summary.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
print(json.dumps({k: out[k] for k in ("latency", "loss")}, indent=1))
print({m: res[m][1]["outliers"] for m in res})
