"""
Collector: MQTT subscribe -> validate -> khử trùng lặp -> phát hiện mất gói -> ghi InfluxDB.

Chạy:   python -m src.collector
Dừng:   Ctrl+C (flush nốt dữ liệu còn trong bộ đệm)
"""
import json
import logging
import math
import signal
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import threading
import time
from collections import OrderedDict
from pathlib import Path

from . import config as cfgmod

log = logging.getLogger("collector")

REQUIRED = ("temperature", "humidity", "distance_cm", "sequence", "uptime_s")
FIELDS_FLOAT = ("temperature", "humidity", "distance_cm", "rssi")


class Stats:
    def __init__(self):
        self.received = self.accepted = self.invalid = self.duplicate = 0
        self.lost = self.reboots = self.write_errors = self.spooled = 0
        self.lat_sum = 0.0
        self.lat_n = 0

    def line(self):
        lat = f"{self.lat_sum / self.lat_n:.0f} ms" if self.lat_n else "n/a"
        return (f"rx={self.received} ok={self.accepted} invalid={self.invalid} dup={self.duplicate} "
                f"lost={self.lost} reboot={self.reboots} write_err={self.write_errors} "
                f"spooled={self.spooled} avg_latency={lat}")


def make_limits(cfg):
    v = cfg["VALIDATION"]
    return {
        "temperature": (float(v["temperature_min"]), float(v["temperature_max"])),
        "humidity": (float(v["humidity_min"]), float(v["humidity_max"])),
        "distance_cm": (float(v["distance_cm_min"]), float(v["distance_cm_max"])),
        "rssi": (float(v["rssi_min"]), float(v["rssi_max"])),
    }


def validate(raw, limits):
    """Trả (dict, None) nếu hợp lệ, hoặc (None, lý do). Chỉ loại giá trị KHÔNG THỂ có về vật lý;
    outlier thống kê vẫn được giữ lại để bước tiền xử lý xử lý."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None, "json_parse_error"
    if not isinstance(msg, dict):
        return None, "payload_not_object"
    for k in REQUIRED:
        if k not in msg or msg[k] is None:
            return None, f"missing_{k}"
    for k, (lo, hi) in limits.items():
        if k not in msg:
            continue
        val = msg[k]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            return None, f"bad_type_{k}"
        if not lo <= val <= hi:
            return None, f"out_of_range_{k}"
    try:
        msg["sequence"] = int(msg["sequence"])
        msg["uptime_s"] = int(msg["uptime_s"])
    except (ValueError, TypeError):
        return None, "bad_type_sequence"
    return msg, None


class Collector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.limits = make_limits(cfg)
        self.stats = Stats()
        self.seen = OrderedDict()                 # LRU khử trùng lặp
        self.seen_max = int(cfg["PIPELINE"]["dedupe_cache"])
        self.state = {}                           # device -> (last_seq, last_uptime)
        self.location = cfg["PIPELINE"]["location"]
        self.bucket = cfg["INFLUX"]["bucket_raw"]
        self.spool = Path(cfg["PIPELINE"]["spool_file"])
        self.spool.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._init_influx()

    # ---------- InfluxDB ----------
    def _init_influx(self):
        from . import influx_io
        from influxdb_client.client.write_api import WriteOptions
        self.client = influx_io.get_client(self.cfg)
        opts = WriteOptions(batch_size=50, flush_interval=2_000, jitter_interval=0,
                            retry_interval=5_000, max_retries=3, max_retry_delay=30_000, exponential_base=2)
        self.write_api = self.client.write_api(write_options=opts,
                                               error_callback=self._on_write_error,
                                               success_callback=lambda *_: None)
        self._replay_spool()

    def _on_write_error(self, conf, data, exc):
        """Ghi DB lỗi (DB sập, mạng lỗi...) -> lưu dòng line-protocol xuống đĩa, replay khi khởi động lại."""
        self.stats.write_errors += 1
        text = data.decode() if isinstance(data, bytes) else str(data)
        with self.lock, self.spool.open("a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")
        self.stats.spooled += text.count("\n") + 1
        log.error("write failed (%s) -> spooled", exc)

    def _replay_spool(self):
        if not self.spool.exists() or self.spool.stat().st_size == 0:
            return
        lines = self.spool.read_text(encoding="utf-8").splitlines()
        log.info("replaying %d spooled points", len(lines))
        try:
            from influxdb_client.client.write_api import SYNCHRONOUS
            self.client.write_api(write_options=SYNCHRONOUS).write(
                bucket=self.bucket, org=self.cfg["INFLUX"]["org"], record=lines, write_precision="ms")
            self.spool.write_text("")
        except Exception as e:                    # giữ nguyên file nếu DB vẫn chưa sống
            log.warning("replay failed, keep spool: %s", e)

    # ---------- xử lý một bản tin ----------
    def handle(self, topic, payload, recv_ms=None):
        from influxdb_client import Point, WritePrecision
        recv_ms = recv_ms or int(time.time() * 1000)
        st = self.stats
        st.received += 1
        msg, err = validate(payload, self.limits)
        if err:
            st.invalid += 1
            log.warning("reject (%s): %s", err, payload[:120])
            self._dead_letter(topic, payload, err)
            return None
        parts = topic.split("/")                  # iot/<mssv>/<device>/telemetry
        device = parts[-2] if len(parts) >= 2 else msg.get("device", "unknown")

        ts = msg.get("ts")
        ts_valid = isinstance(ts, (int, float)) and abs(recv_ms - ts) < 86_400_000
        t_ms = int(ts) if ts_valid else recv_ms   # nếu thiết bị chưa có NTP -> dùng giờ nhận
        key = (device, msg["sequence"], int(ts) if ts_valid else msg["uptime_s"])
        if key in self.seen:
            st.duplicate += 1
            return None
        self.seen[key] = 1
        if len(self.seen) > self.seen_max:
            self.seen.popitem(last=False)

        # --- phát hiện mất gói / khởi động lại bằng sequence + uptime_s ---
        seq, up = msg["sequence"], msg["uptime_s"]
        last = self.state.get(device)
        if last:
            last_seq, last_up = last
            if up < last_up or seq < last_seq:
                st.reboots += 1
                log.info("%s rebooted (uptime %s -> %s)", device, last_up, up)
            elif seq > last_seq + 1:
                st.lost += seq - last_seq - 1
        self.state[device] = (seq, up)

        p = (Point("sensor_raw").tag("device", device).tag("location", self.location)
             .field("sequence", seq).field("uptime_s", up))
        for k in FIELDS_FLOAT:
            if k in msg:
                p = p.field(k, float(msg[k]))
        if ts_valid:
            latency = max(recv_ms - ts, 0)
            p = p.field("latency_ms", float(latency))
            st.lat_sum += latency
            st.lat_n += 1
        p = p.time(t_ms, WritePrecision.MS)       # trùng (device, thời gian) => InfluxDB ghi đè, không nhân đôi
        self.write_api.write(bucket=self.bucket, org=self.cfg["INFLUX"]["org"], record=p)
        st.accepted += 1
        return p

    def _dead_letter(self, topic, payload, reason):
        path = self.spool.parent / "rejected.jsonl"
        with self.lock, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "topic": topic, "reason": reason,
                                "payload": payload.decode(errors="replace") if isinstance(payload, bytes) else payload},
                               ensure_ascii=False) + "\n")

    def close(self):
        try:
            self.write_api.close()
        finally:
            self.client.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import paho.mqtt.client as mqtt
    cfg = cfgmod.load()
    col = Collector(cfg)
    m = cfg["MQTT"]

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=m["client_id"], clean_session=False)
    if m.get("username"):
        client.username_pw_set(m["username"], m.get("password") or None)
    client.reconnect_delay_set(min_delay=1, max_delay=30)   # tự kết nối lại khi mất mạng

    def on_connect(c, userdata, flags, rc, props=None):
        if rc == 0:
            log.info("MQTT connected -> subscribe %s", m["topic"])
            c.subscribe(m["topic"], qos=int(m["qos"]))      # subscribe lại sau mỗi lần reconnect
            c.subscribe(m["topic"].replace("telemetry", "status"), qos=1)   # LWT online/offline
        else:
            log.error("MQTT connect failed: %s", rc)

    def on_disconnect(c, userdata, flags, rc, props=None):
        log.warning("MQTT disconnected (%s) - auto reconnect", rc)

    def on_message(c, userdata, msg):
        if msg.topic.endswith("/status"):
            log.info("status %s = %s", msg.topic, msg.payload.decode(errors="replace"))
            return
        try:
            col.handle(msg.topic, msg.payload)
        except Exception:
            log.exception("handle error")

    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
    client.connect_async(m["host"], int(m["port"]), keepalive=30)
    client.loop_start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    interval = int(cfg["PIPELINE"]["stats_interval_s"])
    while not stop.wait(interval):
        log.info("stats: %s", col.stats.line())
    client.loop_stop()
    client.disconnect()
    col.close()
    log.info("final: %s", col.stats.line())


if __name__ == "__main__":
    sys.exit(main())
