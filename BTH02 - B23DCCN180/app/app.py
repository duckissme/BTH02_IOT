"""
App giám sát IoT (Streamlit).  Chạy:  streamlit run app/app.py
 - Tab 1: dữ liệu real-time (sensor_raw), tự làm mới
 - Tab 2: dữ liệu đã tiền xử lý (sensor_clean) + chạy tiền xử lý ngay trên giao diện
 - Tab 3: sức khỏe pipeline: độ trễ end-to-end, mất gói, RSSI
Nếu chưa có InfluxDB: chọn "Demo (CSV)" ở thanh bên để chạy với dữ liệu giả lập.
"""
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import config as cfgmod, influx_io  # noqa: E402
from src.metrics import latency_summary, loss_summary  # noqa: E402
from src.preprocess_core import SENSOR_COLS, preprocess  # noqa: E402

st.set_page_config(page_title="IoT Monitor - B23DCCN180", layout="wide")
cfg = cfgmod.load()
P = cfg["PREPROCESS"]

with st.sidebar:
    st.header("Nguồn dữ liệu")
    source = st.radio("Chế độ", ["InfluxDB", "Demo (CSV)"])
    device = st.text_input("Device", "esp32-01")
    window = st.selectbox("Khoảng thời gian", ["-10m", "-30m", "-1h", "-3h", "-24h"], index=2)
    refresh = st.slider("Làm mới (giây)", 2, 30, 5)
    method = st.selectbox("Phát hiện outlier", ["iqr", "zscore", "hampel"],
                          index=["iqr", "zscore", "hampel"].index(P["outlier_method"]))
    freq = st.selectbox("Cửa sổ resample", ["10s", "30s", "1min", "5min"], index=1)


@st.cache_resource
def client():
    return influx_io.get_client(cfg)


def load_raw():
    if source == "Demo (CSV)":
        path = ROOT / "data" / "sim_raw.csv"
        if not path.exists():
            st.error("Chưa có data/sim_raw.csv. Chạy: python -m src.simulate_publisher --csv data/sim_raw.csv")
            st.stop()
        return pd.read_csv(path, index_col=0, parse_dates=True)
    try:
        return influx_io.query_df(client(), cfg, cfg["INFLUX"]["bucket_raw"], "sensor_raw", window, device=device)
    except Exception as e:
        st.error(f"Không truy vấn được InfluxDB: {e}")
        st.stop()


def line_chart(df, cols, title):
    d = df[cols].reset_index().melt("_time", var_name="metric", value_name="value")
    fig = px.line(d, x="_time", y="value", color="metric", title=title)
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=40, b=10), legend_title_text="")
    st.plotly_chart(fig, use_container_width=True)


@st.fragment(run_every=refresh)
def realtime_tab():
    raw = load_raw()
    if raw.empty:
        st.warning("Chưa có dữ liệu trong khoảng thời gian này.")
        return
    last = raw.iloc[-1]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Nhiệt độ (°C)", f"{last['temperature']:.1f}")
    c2.metric("Độ ẩm (%RH)", f"{last['humidity']:.1f}")
    c3.metric("Khoảng cách (cm)", f"{last['distance_cm']:.1f}")
    age = (pd.Timestamp.now(tz="UTC") - raw.index[-1]).total_seconds()
    c4.metric("Tuổi bản tin cuối (s)", f"{age:.0f}", help="> 3 chu kỳ gửi => nghi ngờ thiết bị mất kết nối")
    line_chart(raw, ["temperature", "humidity"], "Nhiệt độ & độ ẩm (dữ liệu thô)")
    line_chart(raw, ["distance_cm"], "Khoảng cách (cm)")


with st.sidebar:
    st.caption("Tab Real-time tự làm mới; hai tab còn lại tính theo yêu cầu.")

st.title("Giám sát dữ liệu IoT: ESP32 + DHT22 + HC-SR04")
tab1, tab2, tab3 = st.tabs(["Real-time", "Đã tiền xử lý", "Độ trễ & chất lượng"])

with tab1:
    realtime_tab()

with tab2:
    raw = load_raw()
    if raw.empty:
        st.warning("Chưa có dữ liệu.")
    else:
        clean, rep = preprocess(raw, freq=freq, method=method, iqr_k=float(P["iqr_k"]),
                                z_thresh=float(P["z_threshold"]), interp_limit=int(P["interp_limit"]),
                                rolling_window=int(P["rolling_window"]))
        a, b, c, d = st.columns(4)
        a.metric("Dòng thô → cửa sổ", f"{rep['rows_in']} → {rep['rows_out']}")
        b.metric("Outlier loại bỏ", sum(rep["outliers"].values()))
        c.metric("Cửa sổ nội suy", rep["windows_imputed"])
        d.metric("Khoảng trống giữ nguyên", rep["windows_gap"])
        sel = st.selectbox("Đại lượng", SENSOR_COLS)
        both = pd.concat({"thô": raw[sel].resample(freq).mean(), "đã xử lý": clean[sel],
                          "rolling mean": clean[f"{sel}_rollmean"]}, axis=1)
        line_chart(both, list(both.columns), f"{sel}: thô vs đã xử lý ({freq})")
        line_chart(clean, [f"{c}_minmax" for c in SENSOR_COLS if f"{c}_minmax" in clean], "Chuẩn hóa Min-Max [0,1]")
        with st.expander("Bảng dữ liệu & tham số chuẩn hóa"):
            st.dataframe(clean.tail(50))
            st.json(rep["scalers"])
        if source == "InfluxDB" and st.button("Ghi kết quả vào iot_clean/sensor_clean"):
            out = clean.copy()
            out[["is_imputed", "is_gap"]] = out[["is_imputed", "is_gap"]].astype(int)
            out["device"] = device
            influx_io.write_df(client(), cfg, cfg["INFLUX"]["bucket_clean"], "sensor_clean",
                               out.dropna(subset=["temperature"]))
            st.success("Đã ghi.")

with tab3:
    raw = load_raw()
    lat, loss = latency_summary(raw), loss_summary(raw)
    if lat:
        cols = st.columns(5)
        for col, (k, label) in zip(cols, [("mean", "TB"), ("p50", "P50"), ("p95", "P95"), ("p99", "P99"), ("max", "Max")]):
            col.metric(f"Độ trễ {label} (ms)", f"{lat[k]:.0f}")
        st.plotly_chart(px.histogram(raw, x="latency_ms", nbins=60, title="Phân bố độ trễ end-to-end (ms)"),
                        use_container_width=True)
    else:
        st.info("Chưa có trường latency_ms (thiết bị chưa đồng bộ NTP hoặc chưa gửi 'ts').")
    if loss:
        a, b, c = st.columns(3)
        a.metric("Bản tin nhận", loss["received"])
        b.metric("Mất gói (theo sequence)", f"{loss['lost']} ({loss['loss_rate_pct']:.1f}%)")
        c.metric("Số lần khởi động lại", loss["reboots"])
    if "rssi" in raw:
        line_chart(raw, ["rssi"], "RSSI (dBm)")
