"""Hàm tiện ích đọc/ghi InfluxDB 2.x dùng chung cho collector, preprocess và app."""
import pandas as pd


def get_client(cfg):
    from influxdb_client import InfluxDBClient
    s = cfg["INFLUX"]
    if not s.get("token"):
        raise RuntimeError("Thiếu token InfluxDB: đặt biến môi trường IOT_INFLUX_TOKEN")
    return InfluxDBClient(url=s["url"], token=s["token"], org=s["org"], timeout=15_000)


def ensure_buckets(cfg, raw_days=30, clean_days=365):
    """Tạo bucket + retention policy (raw: 30 ngày, clean: 365 ngày) nếu chưa có."""
    from influxdb_client.domain.bucket_retention_rules import BucketRetentionRules
    client = get_client(cfg)
    api = client.buckets_api()
    for name, days in ((cfg["INFLUX"]["bucket_raw"], raw_days), (cfg["INFLUX"]["bucket_clean"], clean_days)):
        rule = BucketRetentionRules(type="expire", every_seconds=days * 86400)
        existing = api.find_bucket_by_name(name)
        if existing is None:
            api.create_bucket(bucket_name=name, retention_rules=rule, org=cfg["INFLUX"]["org"])
            print(f"[+] created bucket {name} (retention {days}d)")
        else:
            existing.retention_rules = [rule]
            api.update_bucket(existing)
            print(f"[=] bucket {name} exists -> retention set to {days}d")
    client.close()


def query_df(client, cfg, bucket, measurement, start="-1h", stop=None, device=None):
    """Trả DataFrame index=UTC datetime, mỗi field là một cột (pivot)."""
    stop_clause = f", stop: {stop}" if stop else ""
    dev = f'|> filter(fn: (r) => r.device == "{device}")' if device else ""
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: {start}{stop_clause})
  |> filter(fn: (r) => r._measurement == "{measurement}")
  {dev}
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
    res = client.query_api().query_data_frame(flux, org=cfg["INFLUX"]["org"])
    if isinstance(res, list):
        res = pd.concat(res) if res else pd.DataFrame()
    if res.empty:
        return res
    res = res.drop(columns=[c for c in ("result", "table", "_start", "_stop", "_measurement") if c in res.columns])
    res["_time"] = pd.to_datetime(res["_time"], utc=True)
    return res.set_index("_time").sort_index()


def write_df(client, cfg, bucket, measurement, df, tag_columns=("device",)):
    from influxdb_client.client.write_api import SYNCHRONOUS
    api = client.write_api(write_options=SYNCHRONOUS)
    api.write(bucket=bucket, org=cfg["INFLUX"]["org"], record=df,
              data_frame_measurement_name=measurement,
              data_frame_tag_columns=[c for c in tag_columns if c in df.columns])
