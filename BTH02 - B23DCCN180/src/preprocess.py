"""
CLI tiền xử lý.
  Từ InfluxDB:  python -m src.preprocess --source influx --start -2h --write-influx
  Từ CSV:       python -m src.preprocess --source csv --csv data/sim_raw.csv --out data/clean.csv
"""
import argparse
import json
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import pandas as pd

from . import config as cfgmod
from .metrics import latency_summary, loss_summary
from .preprocess_core import preprocess


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["influx", "csv"], default="influx")
    ap.add_argument("--csv")
    ap.add_argument("--start", default="-1h", help="Flux range start, vd -2h hoặc 2026-10-18T00:00:00Z")
    ap.add_argument("--stop")
    ap.add_argument("--device", default="esp32-01")
    ap.add_argument("--out")
    ap.add_argument("--report", help="ghi báo cáo tiền xử lý ra file JSON")
    ap.add_argument("--write-influx", action="store_true", help="ghi kết quả vào measurement sensor_clean")
    a = ap.parse_args(argv)

    cfg = cfgmod.load()
    p = cfg["PREPROCESS"]
    client = None
    if a.source == "influx":
        from . import influx_io
        client = influx_io.get_client(cfg)
        raw = influx_io.query_df(client, cfg, cfg["INFLUX"]["bucket_raw"], "sensor_raw", a.start, a.stop, a.device)
    else:
        raw = pd.read_csv(a.csv, index_col=0, parse_dates=True)
    if raw.empty:
        print("Không có dữ liệu trong khoảng thời gian đã chọn.")
        return 1

    clean, rep = preprocess(raw, freq=p["resample"], method=p["outlier_method"], iqr_k=float(p["iqr_k"]),
                            z_thresh=float(p["z_threshold"]), interp_limit=int(p["interp_limit"]),
                            rolling_window=int(p["rolling_window"]))
    rep["latency"], rep["loss"] = latency_summary(raw), loss_summary(raw)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2, ensure_ascii=False)
    if a.out:
        clean.to_csv(a.out)
    if a.write_influx:
        from . import influx_io
        out = clean.copy()
        for c in ("is_imputed", "is_gap"):
            out[c] = out[c].astype(int)          # tránh xung đột kiểu dữ liệu field
        out["device"] = a.device
        out = out.dropna(subset=["temperature"])
        influx_io.write_df(client, cfg, cfg["INFLUX"]["bucket_clean"], "sensor_clean", out)
        print(f"Đã ghi {len(out)} điểm vào {cfg['INFLUX']['bucket_clean']}/sensor_clean")
    if client:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
