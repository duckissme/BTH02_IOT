"""Chạy:  pytest -q   (không cần MQTT/InfluxDB)"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.collector import make_limits, validate  # noqa: E402
from src.config import load  # noqa: E402
from src.metrics import loss_summary  # noqa: E402
from src.preprocess_core import detect_outliers, preprocess  # noqa: E402
from src.simulate_publisher import generate_messages, to_raw_dataframe  # noqa: E402


def _raw():
    return to_raw_dataframe(generate_messages(minutes=60, seed=1))


def test_validate_ok_and_rejects():
    lim = make_limits(load())
    ok = b'{"temperature":25,"humidity":50,"distance_cm":10,"sequence":1,"uptime_s":5}'
    assert validate(ok, lim)[1] is None
    assert validate(b"not json", lim)[1] == "json_parse_error"
    assert validate(b'{"temperature":999,"humidity":50,"distance_cm":10,"sequence":1,"uptime_s":5}', lim)[1] \
        == "out_of_range_temperature"
    assert validate(b'{"temperature":null,"humidity":50,"distance_cm":10,"sequence":1,"uptime_s":5}', lim)[1] \
        == "missing_temperature"


def test_hampel_finds_injected_spike():
    s = pd.Series(np.sin(np.linspace(0, 6, 200)) * 10 + 50)
    s.iloc[100] += 80
    assert detect_outliers(s, "hampel").iloc[100]


def test_preprocess_pipeline_shapes_and_scaling():
    clean, rep = preprocess(_raw(), freq="30s", method="hampel")
    assert rep["rows_out"] == len(clean) > 0
    assert "temperature_rollmean" in clean and "humidity_delta" in clean
    mm = clean["temperature_minmax"].dropna()
    assert mm.min() >= -1e-9 and mm.max() <= 1 + 1e-9
    assert not clean[["temperature", "humidity", "distance_cm"]].loc[~clean["is_gap"]].isna().any().any()


def test_long_gap_is_not_interpolated():
    raw = _raw()
    clean, rep = preprocess(raw, freq="30s", interp_limit=2)
    assert rep["windows_gap"] >= 1        # khoảng mất kết nối 3 phút phải để trống, không bịa dữ liệu


def test_loss_detection():
    d = pd.DataFrame({"sequence": [1, 2, 5, 6, 1, 2], "uptime_s": [5, 10, 25, 30, 5, 10]},
                     index=pd.date_range("2026-01-01", periods=6, freq="5s"))
    r = loss_summary(d)
    assert r["lost"] == 2 and r["reboots"] == 1


def test_short_gap_is_interpolated():
    idx = pd.date_range("2026-01-01", periods=200, freq="5s", tz="UTC")
    df = pd.DataFrame({"temperature": np.linspace(20, 30, 200), "humidity": 50.0, "distance_cm": 100.0}, index=idx)
    df = df.drop(df.index[60:72])                      # mất 1 phút liên tục (2 cửa sổ 30 s) < giới hạn
    clean, rep = preprocess(df, freq="30s", interp_limit=4)
    assert rep["windows_imputed"] >= 1 and rep["windows_gap"] == 0
