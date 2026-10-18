"""Kiểm tra môi trường trước khi chạy:  python check_env.py"""
import importlib, socket, struct, sys
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
from urllib.parse import urlparse
sys.path.insert(0, ".")
ok = True
def say(good, msg, fix=""):
    global ok
    ok &= good
    print(("[ OK ] " if good else "[LỖI] ") + msg + (f"\n       -> {fix}" if not good and fix else ""))

say(sys.version_info >= (3, 9), f"Python {sys.version.split()[0]}", "Cần Python >= 3.9 (khuyên dùng 3.11 hoặc 3.12)")
say(struct.calcsize("P") * 8 == 64, f"Python {struct.calcsize(chr(80)) * 8}-bit", "Cài Python 64-bit (bản 32-bit hay lỗi khi cài pandas/scikit-learn)")
for m, pipname in [("pandas","pandas"),("numpy","numpy"),("sklearn","scikit-learn"),("paho.mqtt.client","paho-mqtt"),
                   ("influxdb_client","influxdb-client"),("streamlit","streamlit"),("plotly","plotly")]:
    try: importlib.import_module(m); say(True, f"thư viện {pipname}")
    except Exception: say(False, f"thiếu thư viện {pipname}", "chạy setup.bat (hoặc pip install -r requirements.txt)")

from src import config
cfg = config.load()
host, port = cfg["MQTT"]["host"], int(cfg["MQTT"]["port"])
try:
    socket.create_connection((host, port), timeout=5).close(); say(True, f"MQTT broker {host}:{port} truy cập được")
except Exception as e:
    say(False, f"không nối được broker {host}:{port} ({e})", "kiểm tra mạng/firewall hoặc đổi host trong config/config.ini")

token = cfg["INFLUX"].get("token")
say(bool(token), "đã có token InfluxDB", "tạo file .env từ .env.example và dán token vào IOT_INFLUX_TOKEN")
if token:
    try:
        from influxdb_client import InfluxDBClient
        c = InfluxDBClient(url=cfg["INFLUX"]["url"], token=token, org=cfg["INFLUX"]["org"], timeout=8000)
        say(c.ping(), f"InfluxDB {cfg['INFLUX']['url']} phản hồi", "InfluxDB chưa chạy hoặc sai IOT_INFLUX_URL")
        names = {b.name for b in c.buckets_api().find_buckets().buckets}
        for b in (cfg["INFLUX"]["bucket_raw"], cfg["INFLUX"]["bucket_clean"]):
            say(b in names, f"bucket {b}", "tạo bucket trên giao diện InfluxDB hoặc chạy: python -c \"from src import config, influx_io; influx_io.ensure_buckets(config.load(), 30, 30)\"")
    except Exception as e:
        say(False, f"lỗi truy cập InfluxDB: {e}", "kiểm tra url, org, token")
print("\n=> SẴN SÀNG. Chạy run_collector rồi run_app." if ok else "\n=> Còn lỗi ở trên, sửa rồi chạy lại check_env.py")
