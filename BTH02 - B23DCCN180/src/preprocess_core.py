"""
Lõi tiền xử lý (thuần pandas/numpy/sklearn, không phụ thuộc InfluxDB nên test được offline).

Thứ tự bước:  làm sạch -> phát hiện outlier -> resample -> xử lý missing -> đặc trưng -> chuẩn hóa
(outlier được đặt NaN TRƯỚC khi resample để giá trị rác không kéo lệch trung bình cửa sổ).
"""
import numpy as np
import pandas as pd

SENSOR_COLS = ["temperature", "humidity", "distance_cm"]
HARD_LIMITS = {"temperature": (-40, 80), "humidity": (0, 100), "distance_cm": (2, 400)}


def detect_outliers(s: pd.Series, method="iqr", k=1.5, z=3.0) -> pd.Series:
    """Trả mask True = outlier."""
    x = s.dropna()
    if len(x) < 8:
        return pd.Series(False, index=s.index)
    if method == "hampel":
        # Hampel: so sánh với trung vị trượt (cửa sổ 21 mẫu, ngưỡng 5 MAD) -> bắt được spike cả khi tín hiệu biến thiên chậm
        med = s.rolling(21, center=True, min_periods=3).median()
        mad = (s - med).abs().rolling(21, center=True, min_periods=3).median() * 1.4826
        floor = 0.1 * x.std(ddof=0) + 1e-9          # tránh MAD=0 khi cảm biến đứng yên (vd Wokwi)
        return (s - med).abs() > 5.0 * mad.clip(lower=floor)
    if method == "zscore":
        sd = x.std(ddof=0)
        if sd == 0:
            return pd.Series(False, index=s.index)
        return ((s - x.mean()).abs() / sd) > z
    q1, q3 = x.quantile([0.25, 0.75])
    iqr = q3 - q1
    return (s < q1 - k * iqr) | (s > q3 + k * iqr)


def preprocess(raw: pd.DataFrame, freq="30s", method="iqr", iqr_k=1.5, z_thresh=3.0,
               interp_limit=4, rolling_window=5):
    """raw: index = datetime UTC, cột temperature/humidity/distance_cm/... (nhiều device -> gọi theo từng device).
    Trả (clean_df, report_dict)."""
    rep = {"rows_in": int(len(raw))}
    df = raw.copy()
    df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()

    # 1) làm sạch: bỏ trùng timestamp, ép số, loại giá trị ngoài miền vật lý
    dup = int(df.index.duplicated(keep="last").sum())
    df = df[~df.index.duplicated(keep="last")]
    rep["duplicates_dropped"] = dup
    for c in SENSOR_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        lo, hi = HARD_LIMITS[c]
        df.loc[(df[c] < lo) | (df[c] > hi), c] = np.nan
    rep["missing_before"] = {c: int(df[c].isna().sum()) for c in SENSOR_COLS}

    # 2) outlier -> NaN
    rep["outliers"] = {}
    for c in SENSOR_COLS:
        mask = detect_outliers(df[c], method, iqr_k, z_thresh)
        rep["outliers"][c] = int(mask.sum())
        df.loc[mask, c] = np.nan

    # 3) resample theo cửa sổ thời gian (trung bình) + đếm số mẫu thô trong mỗi cửa sổ
    agg = df[SENSOR_COLS].resample(freq).mean()
    agg["n_raw"] = df["temperature"].resample(freq).count().astype(int)
    for extra in ("rssi", "latency_ms"):
        if extra in df.columns:
            agg[extra] = df[extra].resample(freq).mean()
    rep["windows"] = int(len(agg))

    # 4) missing: nội suy theo thời gian, tối đa interp_limit cửa sổ liên tiếp (khoảng trống dài giữ NaN)
    imputed = agg[SENSOR_COLS].isna()
    for c in SENSOR_COLS:
        na = agg[c].isna()
        run_len = na.groupby((na != na.shift()).cumsum()).transform("sum")
        too_long = na & (run_len > interp_limit)          # cả khoảng trống dài hơn giới hạn đều giữ NaN
        agg[c] = agg[c].interpolate(method="time", limit_area="inside").mask(too_long)
    still_nan = agg[SENSOR_COLS].isna()
    agg["is_imputed"] = (imputed & ~still_nan).any(axis=1)
    agg["is_gap"] = still_nan.any(axis=1)
    rep["windows_imputed"] = int(agg["is_imputed"].sum())
    rep["windows_gap"] = int(agg["is_gap"].sum())

    # 5) đặc trưng: rolling mean, độ lệch chuẩn trượt, delta (đạo hàm rời rạc)
    for c in SENSOR_COLS:
        agg[f"{c}_rollmean"] = agg[c].rolling(rolling_window, min_periods=1).mean()
        agg[f"{c}_delta"] = agg[c].diff()
    agg["temperature_rollstd"] = agg["temperature"].rolling(rolling_window, min_periods=2).std()

    # 6) chuẩn hóa: Min-Max [0,1] và Z-score (tham số lưu lại để dùng lại khi suy luận)
    from sklearn.preprocessing import MinMaxScaler, StandardScaler
    ok = ~agg["is_gap"]
    scalers = {}
    for name, sc in (("minmax", MinMaxScaler()), ("z", StandardScaler())):
        vals = agg.loc[ok, SENSOR_COLS]
        if len(vals) >= 2:
            sc.fit(vals)
            agg.loc[ok, [f"{c}_{name}" for c in SENSOR_COLS]] = sc.transform(vals)
            scalers[name] = ({"min": sc.data_min_.tolist(), "max": sc.data_max_.tolist()} if name == "minmax"
                             else {"mean": sc.mean_.tolist(), "std": sc.scale_.tolist()})
    rep["scalers"] = scalers
    rep["rows_out"] = int(len(agg))
    return agg, rep
