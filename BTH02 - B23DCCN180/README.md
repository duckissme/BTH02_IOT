# Bài thực hành 2 – Thu thập, lưu trữ và tiền xử lý dữ liệu IoT

Sinh viên: Nguyễn Minh Đức – B23DCCN180 – Lớp IOT-05-01
Kế thừa Bài 1 (ESP32 + DHT22 + HC-SR04 trên Wokwi, cùng sơ đồ chân và các khóa `temperature, humidity, distance_cm, rssi, sequence, uptime_s`).

## Kiến trúc
```
DHT22 + HC-SR04 -> ESP32 (Wokwi) --MQTT--> Broker --subscribe--> collector.py --> InfluxDB (iot_raw)
                                                                                     |
                                              preprocess.py <------ đọc / ghi clean --+--> iot_clean
                                                                                     |
                                                        Grafana (:3000) / Streamlit app (:8501)
```

## Cấu trúc thư mục
| Đường dẫn | Nội dung |
|---|---|
| `firmware/` | `sketch.ino`, `diagram.json`, `libraries.txt` (dán vào Wokwi) |
| `src/collector.py` | Subscribe MQTT, validate, khử trùng lặp, đếm mất gói, đo độ trễ, ghi InfluxDB, spool khi DB lỗi |
| `src/preprocess_core.py`, `src/preprocess.py` | Làm sạch, outlier (IQR / Z-score / Hampel), resample, nội suy, rolling/delta, chuẩn hóa |
| `src/metrics.py` | Độ trễ P50/P95/P99, tỉ lệ mất gói |
| `src/simulate_publisher.py` | Giả lập ESP32 "bẩn" để test khi chưa có Wokwi |
| `app/app.py` | Ứng dụng Streamlit (real-time, đã xử lý, độ trễ) |
| `config/config.ini` | Cấu hình broker, DB, ngưỡng validate, tham số tiền xử lý |
| `docker-compose.yml`, `grafana/` | InfluxDB 2.7 + Grafana (+ Mosquitto tùy chọn) |
| `tests/` | Unit test phần validate và tiền xử lý (không cần MQTT/DB) |
| `docs/` | Hình và số liệu dùng trong báo cáo |

## Hướng dẫn chạy
1. **Cài đặt**
   ```bash
   python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env        # sửa mật khẩu và token; KHÔNG commit file .env
   ```
2. **Khởi động DB và Grafana**
   ```bash
   docker compose up -d
   export IOT_INFLUX_TOKEN=$(grep ^INFLUXDB_TOKEN .env | cut -d= -f2)   # Windows: set IOT_INFLUX_TOKEN=...
   python -c "from src import config, influx_io; influx_io.ensure_buckets(config.load())"   # tạo iot_clean, đặt retention
   ```
3. **Thiết bị**: tạo dự án ESP32 trên Wokwi, dán 3 file trong `firmware/`. Đổi `STUDENT_ID`/`DEVICE_ID` nếu cần
   (khớp `topic` trong `config/config.ini`). Wokwi không truy cập được `localhost`, nên dùng broker công cộng
   `broker.emqx.io` (hoặc HiveMQ Cloud). Bấm Play.
   *Chưa có Wokwi?* chạy `docker compose --profile local-broker up -d`, đặt `host = localhost` trong config rồi:
   `python -m src.simulate_publisher --minutes 20 --interval 1`
4. **Thu thập**: `python -m src.collector` (log thống kê mỗi 30 s: rx, ok, invalid, dup, lost, avg_latency).
5. **Tiền xử lý**
   ```bash
   python -m src.preprocess --source influx --start -2h --write-influx --report data/report.json
   ```
   Chạy offline bằng CSV: `python -m src.simulate_publisher --csv data/sim_raw.csv` rồi
   `python -m src.preprocess --source csv --csv data/sim_raw.csv --out data/sim_clean.csv`.
6. **Dashboard / App**: `streamlit run app/app.py` (http://localhost:8501). Chọn "Demo (CSV)" nếu chưa có DB.
   Grafana: http://localhost:3000, datasource `InfluxDB-IoT` đã được provision sẵn (xem truy vấn Flux mẫu bên dưới).
7. **Test**: `pytest -q`

## Schema InfluxDB
| Bucket / measurement | Tags | Fields |
|---|---|---|
| `iot_raw` (30 ngày) / `sensor_raw` | `device`, `location` | `temperature`, `humidity`, `distance_cm`, `rssi`, `sequence`, `uptime_s`, `latency_ms` |
| `iot_clean` (365 ngày) / `sensor_clean` | `device` | `temperature`, `humidity`, `distance_cm` (+ `_rollmean`, `_delta`, `_minmax`, `_z`), `temperature_rollstd`, `n_raw`, `is_imputed`, `is_gap` |

Timestamp là `ts` do ESP32 gắn (NTP). Hai bản ghi cùng `measurement + tags + timestamp` bị InfluxDB ghi đè, nên bản tin
QoS 1 gửi lặp không tạo bản sao (collector còn có LRU cache để đếm số bản trùng).

## Flux mẫu cho Grafana
```flux
from(bucket: "iot_raw") |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "sensor_raw" and r._field == "temperature")
from(bucket: "iot_raw") |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "sensor_raw" and r._field == "latency_ms")
  |> aggregateWindow(every: 1m, fn: mean)
```

## Đo độ trễ end-to-end
`latency_ms = thời điểm collector nhận − ts (ESP32 gắn sau khi đồng bộ NTP)`. Sai số đồng hồ giữa ESP32 và máy chạy
collector làm lệch kết quả cỡ vài chục ms; nên đồng bộ NTP cho máy collector.

## Bảo mật
Token InfluxDB chỉ nằm trong `.env`/biến môi trường. Broker công cộng và cổng 1883 không mã hóa; khi triển khai thật
dùng MQTT over TLS (cổng 8883), username/password riêng và ACL theo topic.

## Chạy nhanh KHÔNG cần Docker
1. Cài Python 3.9+. Windows: bấm đúp `setup.bat` (Linux/macOS: `./setup.sh`).
2. Có InfluxDB: cài bản 2.x trực tiếp lên máy hoặc dùng InfluxDB Cloud (gói miễn phí). Tạo 2 bucket `iot_raw`, `iot_clean` và một API token.
3. Mở file `.env`, điền `IOT_INFLUX_TOKEN`, `IOT_INFLUX_URL`, `IOT_INFLUX_ORG`.
4. `run_check.bat` (hoặc `./run.sh check`): báo chính xác bước nào còn thiếu.
5. Bật Wokwi, rồi `run_collector.bat` và `run_app.bat`. Chưa có Wokwi: `run_simulate.bat` thay cho thiết bị.
6. `run_preprocess.bat` để tiền xử lý và ghi sang `iot_clean`.
7. Không có InfluxDB: `run_demo.bat` chạy app với dữ liệu giả lập.
