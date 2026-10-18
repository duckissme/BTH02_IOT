"""Chỉ số chất lượng pipeline: độ trễ end-to-end, tỉ lệ mất gói, trùng lặp, mật độ lấy mẫu."""
import numpy as np
import pandas as pd


def latency_summary(df: pd.DataFrame) -> dict:
    """latency_ms = thời điểm collector nhận - ts do ESP32 gắn (NTP). Sai số đồng hồ ~ vài chục ms."""
    if "latency_ms" not in df.columns or df["latency_ms"].dropna().empty:
        return {}
    x = df["latency_ms"].dropna().astype(float)
    return {"n": int(len(x)), "mean": float(x.mean()), "std": float(x.std()),
            "p50": float(x.quantile(.5)), "p95": float(x.quantile(.95)),
            "p99": float(x.quantile(.99)), "max": float(x.max())}


def loss_summary(df: pd.DataFrame) -> dict:
    """Mất gói = khoảng nhảy của 'sequence' trong cùng một lần chạy (uptime_s tăng đơn điệu)."""
    if "sequence" not in df.columns or len(df) < 2:
        return {}
    d = df.sort_index()
    seq = d["sequence"].astype(int).to_numpy()
    up = d["uptime_s"].astype(int).to_numpy() if "uptime_s" in d.columns else np.zeros_like(seq)
    lost = reboots = 0
    for i in range(1, len(seq)):
        if seq[i] < seq[i - 1] or up[i] < up[i - 1]:
            reboots += 1
        elif seq[i] > seq[i - 1] + 1:
            lost += seq[i] - seq[i - 1] - 1
    received = len(seq)
    return {"received": received, "lost": int(lost), "reboots": int(reboots),
            "loss_rate_pct": 100.0 * lost / (received + lost) if received + lost else 0.0}
