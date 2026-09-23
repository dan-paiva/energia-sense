#!/bin/sh
# Gera o arquivo de senhas a partir das variáveis de ambiente a cada início.
set -eu
: "${MQTT_USUARIO:?defina MQTT_USUARIO}"
: "${MQTT_SENHA:?defina MQTT_SENHA}"
rm -f /mosquitto/data/passwd
mosquitto_passwd -b -c /mosquitto/data/passwd "$MQTT_USUARIO" "$MQTT_SENHA"
if [ -n "${MQTT_USUARIO_NO:-}" ]; then
  mosquitto_passwd -b /mosquitto/data/passwd "$MQTT_USUARIO_NO" "$MQTT_SENHA_NO"
fi
chown mosquitto:mosquitto /mosquitto/data/passwd
chmod 0700 /mosquitto/data/passwd
exec mosquitto -c /mosquitto/config/mosquitto.conf
