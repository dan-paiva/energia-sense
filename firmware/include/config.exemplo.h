// Copie para config.h (que fica fora do git) e ajuste para a instalação.
#pragma once

// ---- identificação -----------------------------------------------------------
#define NO_ID "no-01"             // minúsculas, números e hífen; único por nó
#define FIRMWARE_VERSAO "0.1.0"

// ---- rede --------------------------------------------------------------------
#define WIFI_SSID "rede-da-portaria"
#define WIFI_SENHA "senha-do-wifi"

#define MQTT_HOST "192.168.0.10"   // IP ou nome do servidor com o broker
#define MQTT_PORTA 1883
#define MQTT_USUARIO "no-sensor"   // MQTT_USUARIO_NO do .env
#define MQTT_SENHA "troque-esta-senha"
#define MQTT_PREFIXO "energia"

// ---- medição -----------------------------------------------------------------
#define INTERVALO_MS 5000          // uma leitura de todos os canais a cada 5 s

// Barramento Modbus dos PZEM-004T v3 (UART2).
#define PZEM_RX 16
#define PZEM_TX 17

// Canais do nó. O id vira "<NO_ID>.<id>" no servidor; nome e limites são
// cadastrados pelo painel.
//   CANAL_PZEM(id, endereço Modbus)
//   CANAL_SCT(id, pino ADC, ampères por volt do sensor, tensão nominal)
// O SCT-013-030 entrega 1 V em 30 A, então 30 A/V. Use pinos do ADC1 (32 a 39),
// porque o ADC2 não funciona com o Wi-Fi ligado.
#define CANAIS                     \
  CANAL_PZEM("c1", 0x01)           \
  CANAL_PZEM("c2", 0x02)           \
  CANAL_PZEM("c3", 0x03)           \
  CANAL_SCT("c4", 34, 30.0f, 127.0f)

// ---- resiliência -------------------------------------------------------------
// Leituras que não puderam ser enviadas vão para a flash e são reenviadas
// quando a conexão volta. Com 4 canais a cada 5 s, 1,2 MB guardam cerca de 4 horas.
#define FILA_MAX_BYTES (1200 * 1024)
#define WATCHDOG_S 60
