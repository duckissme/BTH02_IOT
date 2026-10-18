"""
Giả lập ESP32 để test pipeline khi chưa có Wokwi/phần cứng.
Tạo dữ liệu có chủ đích "bẩn": mất gói, trùng lặp, outlier, giá trị lỗi, mất kết nối dài, khởi động lại.

  Publish MQTT:   python -m src.simulate_publisher --minutes 20 --interval 1
  Xuất CSV test:  python -m src.simulate_publisher --csv data/sim_raw.csv --minutes 120   (không cần MQTT)
"""
import argparse
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import json
import math
import time

import numpy as np
import pandas as pd


def generate_messages(minutes=120, interval_s=5, loss=0.03, dup=0.01, outlier=0.012, invalid=0.01,
                      gap=(40, 43), reboot_at=75, seed=7, t_end_ms=None, device="esp32-01"):
    """Trả (messages, truth). messages = list[dict] giống payload ESP32; None-giá-trị = cảm biến lỗi."""
    rng = np.random.default_rng(seed)
    n = int(minutes * 60 / interval_s)
    t_end_ms = t_end_ms or int(time.time() * 1000)
    t0 = t_end_ms - n * interval_s * 1000
    msgs, seq, boot_i = [], 0, 0
    for i in range(n):
        t_s = i * interval_s
        if reboot_at is not None and i == int(reboot_at * 60 / interval_s):
            seq, boot_i = 0, i                                   # khởi động lại: sequence & uptime về 0
        seq += 1
        temp = 27 + 3 * math.sin(2 * math.pi * t_s / 3600) + rng.normal(0, 0.15)
        hum = 62 - 8 * math.sin(2 * math.pi * t_s / 3600) + rng.normal(0, 0.6)
        dist = 120 + 60 * math.sin(2 * math.pi * t_s / 900) + rng.normal(0, 0.6)
        if rng.random() < outlier:                               # spike
            col = rng.choice(["temperature", "humidity", "distance_cm"])
            if col == "temperature": temp += rng.choice([-1, 1]) * rng.uniform(12, 25)
            elif col == "humidity": hum += rng.choice([-1, 1]) * rng.uniform(25, 40)
            else: dist += rng.choice([-1, 1]) * rng.uniform(150, 250)
        m = {"device": device, "ts": int(t0 + i * interval_s * 1000 + rng.integers(-30, 30)),
             "sequence": seq, "uptime_s": (i - boot_i) * interval_s,
             "temperature": round(temp, 2), "humidity": round(float(np.clip(hum, 0, 100)), 2),
             "distance_cm": round(float(np.clip(dist, 2, 400)), 2),
             "rssi": int(rng.normal(-65, 4))}
        if rng.random() < invalid:                               # DHT trả NaN -> JSON null / quá miền
            m[rng.choice(["temperature", "humidity"])] = None if rng.random() < .5 else 999
        msgs.append(m)
    # kênh truyền: mất gói ngẫu nhiên, mất kết nối dài, trùng lặp
    g0, g1 = (int(gap[0] * 60 / interval_s), int(gap[1] * 60 / interval_s)) if gap else (-1, -1)
    out = []
    for i, m in enumerate(msgs):
        if g0 <= i < g1 or rng.random() < loss:
            continue
        lat = float(np.clip(rng.lognormal(math.log(120), 0.5), 25, 3000))
        out.append({**m, "_latency_ms": lat})
        if rng.random() < dup:
            out.append({**m, "_latency_ms": lat + rng.uniform(5, 300)})
    return out


def to_raw_dataframe(msgs):
    """Mô phỏng dữ liệu đã nằm trong sensor_raw (sau validate + khử trùng lặp của collector)."""
    rows = []
    for m in msgs:
        if any(m.get(k) is None or not (lo <= m[k] <= hi) for k, (lo, hi) in
               {"temperature": (-40, 80), "humidity": (0, 100), "distance_cm": (2, 400)}.items()):
            continue
        rows.append({"_time": pd.to_datetime(m["ts"], unit="ms", utc=True), "device": m["device"],
                     "temperature": m["temperature"], "humidity": m["humidity"], "distance_cm": m["distance_cm"],
                     "rssi": m["rssi"], "sequence": m["sequence"], "uptime_s": m["uptime_s"],
                     "latency_ms": m["_latency_ms"]})
    df = pd.DataFrame(rows).set_index("_time").sort_index()
    return df[~df.index.duplicated(keep="last")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=120)
    ap.add_argument("--interval", type=float, default=5, help="chu kỳ gửi (s); khi publish thật có thể dùng 1")
    ap.add_argument("--csv", help="chỉ xuất CSV (không dùng MQTT)")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--student", default="b23dccn180")
    ap.add_argument("--device", default="esp32-sim")
    a = ap.parse_args()

    if a.csv:
        msgs = generate_messages(a.minutes, a.interval)
        df = to_raw_dataframe(msgs)
        df.to_csv(a.csv)
        print(f"{len(msgs)} bản tin nhận được -> {len(df)} dòng hợp lệ -> {a.csv}")
        return

    import paho.mqtt.client as mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{a.device}")
    c.connect(a.host, a.port, 30)
    c.loop_start()
    topic = f"iot/{a.student}/{a.device}/telemetry"
    t_now = int(time.time() * 1000)
    msgs = generate_messages(a.minutes, a.interval, t_end_ms=t_now + int(a.minutes * 60_000), device=a.device)
    print(f"Publish {len(msgs)} bản tin lên {a.host}:{a.port} topic {topic}")
    for m in msgs:
        m = {k: v for k, v in m.items() if not k.startswith("_")}
        m["ts"] = int(time.time() * 1000)                       # gửi theo thời gian thực để đo độ trễ
        c.publish(topic, json.dumps(m), qos=1)
        time.sleep(a.interval)
    c.loop_stop()


if __name__ == "__main__":
    main()
