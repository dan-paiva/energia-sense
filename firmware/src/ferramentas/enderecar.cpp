// Grava o endereço Modbus de um PZEM-004T v3.
//
// Vários PZEM dividem o mesmo barramento serial, então cada um precisa de um
// endereço próprio. Ligue UM módulo por vez, grave com
//   pio run -e enderecar -t upload && pio device monitor
// e digite o endereço novo (1 a 247) no monitor serial.

#include <Arduino.h>
#include <PZEM004Tv30.h>

#include "config.h"

// Endereço de broadcast: fala com qualquer PZEM, desde que seja o único no barramento.
static PZEM004Tv30 pzem(Serial2, PZEM_RX, PZEM_TX, 0xF8);

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\nEndereço atual: 0x%02X\n", pzem.readAddress());
  Serial.println("Digite o novo endereço (1 a 247) e tecle Enter:");
}

void loop() {
  if (!Serial.available()) return;
  const long novo = Serial.parseInt();
  while (Serial.available()) Serial.read();
  if (novo < 1 || novo > 247) {
    Serial.println("Endereço inválido.");
    return;
  }
  if (pzem.setAddress((uint8_t)novo)) {
    Serial.printf("Gravado. Endereço lido de volta: 0x%02X\n", pzem.readAddress());
  } else {
    Serial.println("Falha ao gravar. Confira a ligação e se só há um PZEM no barramento.");
  }
}
