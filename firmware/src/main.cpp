// Nó sensor EnergIA Sense.
//
// Lê os canais configurados em config.h a cada INTERVALO_MS, monta um JSON e
// publica em <prefixo>/<nó>/leituras. Sem conexão, a leitura vai para uma fila
// na flash (LittleFS) e é reenviada, com o horário original, quando a conexão
// volta. O servidor descarta duplicatas pela chave (circuito, horário).

#include <Arduino.h>
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <PZEM004Tv30.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <esp_task_wdt.h>
#include <sys/time.h>

#include "config.h"

// ---- canais --------------------------------------------------------------------

enum TipoCanal : uint8_t { TIPO_PZEM, TIPO_SCT };

struct Canal {
  const char *id;
  TipoCanal tipo;
  uint8_t endereco;    // PZEM: endereço Modbus
  uint8_t pino;        // SCT: pino do ADC
  float ampPorVolt;    // SCT: relação do sensor
  float tensaoNominal; // SCT: não mede tensão, usa a nominal para estimar potência
  PZEM004Tv30 *pzem;
};

#define CANAL_PZEM(id, end) {id, TIPO_PZEM, end, 0, 0, 0, nullptr},
#define CANAL_SCT(id, pino, apv, v) {id, TIPO_SCT, 0, pino, apv, v, nullptr},
static Canal canais[] = {CANAIS};
static const size_t N_CANAIS = sizeof(canais) / sizeof(canais[0]);

// ---- estado --------------------------------------------------------------------

static WiFiClient redeTcp;
static PubSubClient mqtt(redeTcp);
static char topicoLeituras[64];
static char topicoStatus[64];
static uint32_t seq = 0;
static uint32_t proximaLeitura = 0;
static uint32_t ultimaTentativaMqtt = 0;

static const char *FILA = "/fila.jsonl";
static const char *FILA_ANTIGA = "/fila.ant.jsonl";
static size_t posFila = 0;
static size_t posFilaAntiga = 0;

// ---- relógio -------------------------------------------------------------------

static bool relogioOk() { return time(nullptr) > 1700000000; }

static uint64_t agoraMs() {
  if (!relogioOk()) return 0;  // o servidor usa a hora de recebimento
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (uint64_t)tv.tv_sec * 1000ULL + tv.tv_usec / 1000;
}

// ---- medição -------------------------------------------------------------------

// Corrente RMS de um SCT-013 com saída em tensão, ligado a um divisor que
// centraliza o sinal em ~1,65 V. Remove o nível DC pela média das amostras.
static float lerCorrenteSct(uint8_t pino, float ampPorVolt) {
  static uint16_t amostras[1600];  // ~5 ciclos de 60 Hz
  const size_t n = sizeof(amostras) / sizeof(amostras[0]);
  uint32_t soma = 0;
  for (size_t i = 0; i < n; i++) {
    amostras[i] = analogReadMilliVolts(pino);
    soma += amostras[i];
  }
  const float media = (float)soma / n;
  double quad = 0;
  for (size_t i = 0; i < n; i++) {
    const float d = amostras[i] - media;
    quad += d * d;
  }
  const float amperes = sqrt(quad / n) / 1000.0f * ampPorVolt;
  return amperes < 0.05f ? 0.0f : amperes;  // abaixo disso é ruído do ADC
}

static void numero(JsonObject o, const char *chave, float v, uint8_t casas) {
  if (isnan(v)) o[chave] = nullptr;
  else o[chave] = serialized(String(v, (unsigned int)casas));
}

static void lerCanal(Canal &c, JsonArray saida) {
  if (c.tipo == TIPO_PZEM) {
    const float i = c.pzem->current();
    const float p = c.pzem->power();
    if (isnan(i) || isnan(p)) {
      Serial.printf("[%s] PZEM 0x%02X sem resposta\n", c.id, c.endereco);
      return;  // sem corrente e potência a leitura não serve
    }
    JsonObject o = saida.add<JsonObject>();
    o["id"] = c.id;
    numero(o, "v", c.pzem->voltage(), 1);
    numero(o, "i", i, 3);
    numero(o, "p", p, 1);
    numero(o, "fp", p > 0 ? c.pzem->pf() : NAN, 2);
    numero(o, "f", c.pzem->frequency(), 1);
    numero(o, "e_kwh", c.pzem->energy(), 3);
  } else {
    const float i = lerCorrenteSct(c.pino, c.ampPorVolt);
    JsonObject o = saida.add<JsonObject>();
    o["id"] = c.id;
    numero(o, "i", i, 3);
    // Potência aparente pela tensão nominal: sem FP, o servidor integra a energia.
    numero(o, "p", i * c.tensaoNominal, 1);
  }
}

static String montarMensagem() {
  JsonDocument doc;
  doc["no"] = NO_ID;
  doc["ts"] = agoraMs();
  doc["seq"] = ++seq;
  doc["intervalo_s"] = INTERVALO_MS / 1000.0f;
  doc["fw"] = FIRMWARE_VERSAO;
  JsonArray circuitos = doc["circuitos"].to<JsonArray>();
  for (size_t k = 0; k < N_CANAIS; k++) {
    lerCanal(canais[k], circuitos);
    mqtt.loop();  // leituras Modbus são lentas; mantém a sessão viva
  }
  String saida;
  if (circuitos.size() > 0) serializeJson(doc, saida);
  return saida;
}

// ---- fila na flash -------------------------------------------------------------

static void enfileirar(const String &linha) {
  File f = LittleFS.open(FILA, FILE_APPEND);
  if (f && f.size() > FILA_MAX_BYTES / 2) {
    // Gira a fila: a antiga é descartada, preservando as leituras mais novas.
    f.close();
    LittleFS.remove(FILA_ANTIGA);
    LittleFS.rename(FILA, FILA_ANTIGA);
    posFilaAntiga = posFila;
    posFila = 0;
    f = LittleFS.open(FILA, FILE_APPEND);
  }
  if (!f) {
    Serial.println("falha ao abrir a fila na flash");
    return;
  }
  f.print(linha);
  f.print('\n');
  f.close();
}

// Reenvia até `limite` linhas do arquivo. Devolve true se o arquivo acabou.
static bool esvaziar(const char *caminho, size_t &pos, int limite) {
  if (!LittleFS.exists(caminho)) return true;
  File f = LittleFS.open(caminho, FILE_READ);
  if (!f) return true;
  f.seek(pos);
  while (limite-- > 0 && f.available()) {
    String linha = f.readStringUntil('\n');
    if (linha.length() > 0 && !mqtt.publish(topicoLeituras, linha.c_str())) {
      f.close();
      return false;
    }
    pos = f.position();
  }
  const bool fim = !f.available();
  f.close();
  if (fim) {
    LittleFS.remove(caminho);
    pos = 0;
  }
  return fim;
}

static void esvaziarFila() {
  if (esvaziar(FILA_ANTIGA, posFilaAntiga, 10)) esvaziar(FILA, posFila, 10);
}

// ---- conectividade -------------------------------------------------------------

static void conectarWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_SENHA);
}

static void garantirMqtt() {
  if (mqtt.connected() || WiFi.status() != WL_CONNECTED) return;
  if (millis() - ultimaTentativaMqtt < 5000) return;
  ultimaTentativaMqtt = millis();
  // Last will: se o nó cair, o broker publica "offline" no lugar dele.
  if (mqtt.connect(NO_ID, MQTT_USUARIO, MQTT_SENHA, topicoStatus, 1, true, "offline")) {
    mqtt.publish(topicoStatus, "online", true);
    Serial.println("MQTT conectado");
  } else {
    Serial.printf("MQTT falhou, estado %d\n", mqtt.state());
  }
}

// ---- ciclo principal -----------------------------------------------------------

void setup() {
  Serial.begin(115200);
  Serial.printf("\nEnergIA Sense %s, nó %s, %u canais\n", FIRMWARE_VERSAO, NO_ID, N_CANAIS);

  esp_task_wdt_init(WATCHDOG_S, true);
  esp_task_wdt_add(nullptr);

  if (!LittleFS.begin(true)) Serial.println("LittleFS indisponível: sem fila offline");

  analogSetAttenuation(ADC_11db);
  for (size_t k = 0; k < N_CANAIS; k++) {
    if (canais[k].tipo == TIPO_PZEM)
      canais[k].pzem = new PZEM004Tv30(Serial2, PZEM_RX, PZEM_TX, canais[k].endereco);
  }

  snprintf(topicoLeituras, sizeof(topicoLeituras), "%s/%s/leituras", MQTT_PREFIXO, NO_ID);
  snprintf(topicoStatus, sizeof(topicoStatus), "%s/%s/status", MQTT_PREFIXO, NO_ID);
  mqtt.setServer(MQTT_HOST, MQTT_PORTA);
  mqtt.setBufferSize(2048);
  mqtt.setKeepAlive(30);

  conectarWifi();
  configTzTime("<-03>3", "a.st1.ntp.br", "pool.ntp.org");
  proximaLeitura = millis();
}

void loop() {
  esp_task_wdt_reset();
  garantirMqtt();
  mqtt.loop();

  if ((int32_t)(millis() - proximaLeitura) >= 0) {
    proximaLeitura += INTERVALO_MS;
    if ((int32_t)(millis() - proximaLeitura) > (int32_t)INTERVALO_MS)
      proximaLeitura = millis() + INTERVALO_MS;  // atrasou demais: não tenta compensar

    const String msg = montarMensagem();
    if (msg.length() == 0) return;
    const bool enviado = mqtt.connected() && mqtt.publish(topicoLeituras, msg.c_str());
    // Sem relógio a leitura não tem horário próprio, e reenviá-la depois a dataria errado.
    if (!enviado && relogioOk()) enfileirar(msg);
  }

  if (mqtt.connected()) esvaziarFila();
  delay(10);
}
