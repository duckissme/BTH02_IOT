#include <WiFi.h>
#include <PubSubClient.h>
#include <DHTesp.h>
#include <sys/time.h>

const char* WIFI_SSID     = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";
const char* MQTT_SERVER   = "broker.emqx.io";
const int   MQTT_PORT     = 1883;
const char* MQTT_USER     = "";
const char* MQTT_PASS     = "";
const char* STUDENT_ID    = "b23dccn180";
const char* DEVICE_ID     = "esp32-01";
const unsigned long SEND_INTERVAL_MS = 5000;

const int DHT_PIN = 15, TRIG_PIN = 5, ECHO_PIN = 18, LED_PIN = 2;

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
DHTesp dht;
String topicTelemetry, topicStatus;
unsigned long lastSend = 0, sequenceNo = 0;

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, 6);
  Serial.print("Connecting WiFi");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println(" connected");
}

void syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  Serial.print("Sync NTP");
  time_t t = 0;
  for (int i = 0; i < 40 && t < 1700000000; i++) { delay(500); Serial.print("."); time(&t); }
  Serial.println(t >= 1700000000 ? " ok" : " FAILED (ts se = 0)");
}

int64_t nowMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  if (tv.tv_sec < 1700000000) return 0;          // chưa có NTP
  return (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000;
}

void connectMQTT() {
  while (!mqttClient.connected()) {
    String clientId = String("esp32-") + DEVICE_ID + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    Serial.print("Connecting MQTT...");
    // LWT: broker tự publish "offline" (retained) nếu thiết bị mất kết nối bất thường
    bool ok = mqttClient.connect(clientId.c_str(),
                                 strlen(MQTT_USER) ? MQTT_USER : nullptr,
                                 strlen(MQTT_PASS) ? MQTT_PASS : nullptr,
                                 topicStatus.c_str(), 1, true, "offline");
    if (ok) {
      Serial.println(" connected");
      mqttClient.publish(topicStatus.c_str(), "online", true);
    } else {
      Serial.printf(" failed, rc=%d. Retry in 2 s\n", mqttClient.state());
      delay(2000);
    }
  }
}

float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  if (duration == 0) return NAN;
  return duration * 0.0343f / 2.0f;
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT); pinMode(ECHO_PIN, INPUT); pinMode(LED_PIN, OUTPUT);
  dht.setup(DHT_PIN, DHTesp::DHT22);
  topicTelemetry = String("iot/") + STUDENT_ID + "/" + DEVICE_ID + "/telemetry";
  topicStatus    = String("iot/") + STUDENT_ID + "/" + DEVICE_ID + "/status";
  connectWiFi();
  syncTime();
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setBufferSize(384);
  mqttClient.setKeepAlive(30);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (!mqttClient.connected()) connectMQTT();
  mqttClient.loop();

  unsigned long now = millis();
  if (now - lastSend < SEND_INTERVAL_MS) return;
  lastSend = now;

  TempAndHumidity d = dht.getTempAndHumidity();
  float distance = readDistanceCm();
  if (!isfinite(d.temperature) || !isfinite(d.humidity) || !isfinite(distance)) {
    Serial.println("Invalid sensor data - skip publish");
    return;
  }

  sequenceNo++;
  char payload[320];
  snprintf(payload, sizeof(payload),
           "{\"device\":\"%s\",\"ts\":%lld,\"sequence\":%lu,\"uptime_s\":%lu,"
           "\"temperature\":%.2f,\"humidity\":%.2f,\"distance_cm\":%.2f,\"rssi\":%d}",
           DEVICE_ID, (long long)nowMs(), sequenceNo, millis() / 1000,
           d.temperature, d.humidity, distance, WiFi.RSSI());

  bool ok = mqttClient.publish(topicTelemetry.c_str(), payload);   // PubSubClient chỉ publish QoS 0
  Serial.printf("%s | publish=%s\n", payload, ok ? "OK" : "FAILED");
  digitalWrite(LED_PIN, HIGH); delay(80); digitalWrite(LED_PIN, LOW);
}
