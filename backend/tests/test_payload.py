from datetime import UTC, datetime, timedelta

import pytest

from energia.payload import (
    Contador,
    PayloadInvalido,
    energia_intervalo_wh,
    interpretar,
    resolver_ts,
)

AGORA = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)


def test_interpreta_payload_do_firmware():
    msg = interpretar(
        b'{"no":"no-01","ts":1758650000000,"seq":7,"intervalo_s":5,"fw":"0.1.0",'
        b'"circuitos":[{"id":"c1","v":127.2,"i":3.4,"p":380.5,"fp":0.88,"f":60,"e_kwh":1.5}]}'
    )
    assert msg.no == "no-01"
    assert msg.circuitos[0].p == 380.5


def test_aceita_grandezas_nulas_quando_o_sensor_falha():
    # O ArduinoJson serializa NaN do PZEM desconectado como null.
    msg = interpretar(b'{"no":"no-01","circuitos":[{"id":"c1","v":null,"i":0,"p":0,"fp":null}]}')
    assert msg.circuitos[0].v is None


@pytest.mark.parametrize("bruto", [
    b"nao e json",
    b'{"no":"no-01","circuitos":[]}',
    b'{"no":"NO 01","circuitos":[{"id":"c1","i":1,"p":1}]}',
    b'{"no":"no-01","circuitos":[{"id":"c1","i":-1,"p":1}]}',
    b'{"no":"no-01","circuitos":[{"id":"c1","i":1,"p":1,"fp":1.5}]}',
])
def test_rejeita_payload_invalido(bruto):
    with pytest.raises(PayloadInvalido):
        interpretar(bruto)


def test_ts_zero_usa_hora_de_recebimento():
    assert resolver_ts(0, AGORA) == AGORA


def test_ts_valido_e_mantido_mesmo_atrasado():
    ts = AGORA - timedelta(hours=5)  # leitura vinda do buffer após queda de rede
    assert resolver_ts(int(ts.timestamp() * 1000), AGORA) == ts


def test_ts_absurdo_e_trocado_pela_hora_de_recebimento():
    assert resolver_ts(1000, AGORA) == AGORA
    futuro = AGORA + timedelta(hours=2)
    assert resolver_ts(int(futuro.timestamp() * 1000), AGORA) == AGORA


def test_energia_pelo_contador_quando_confiavel():
    ant = Contador(AGORA, 10.000)
    assert energia_intervalo_wh(ant, AGORA + timedelta(seconds=5), 10.001, 720, 5) == pytest.approx(1)


def test_energia_integrada_na_primeira_leitura():
    assert energia_intervalo_wh(None, AGORA, 10.0, 720, 5) == pytest.approx(1)


def test_energia_integrada_quando_contador_zera():
    ant = Contador(AGORA, 500.0)
    assert energia_intervalo_wh(ant, AGORA + timedelta(seconds=5), 0.0, 360, 5) == pytest.approx(0.5)


def test_energia_integrada_quando_leitura_chega_fora_de_ordem():
    ant = Contador(AGORA, 10.0)
    assert energia_intervalo_wh(ant, AGORA - timedelta(minutes=1), 9.9, 360, 5) == pytest.approx(0.5)


def test_energia_integrada_quando_salto_do_contador_e_implausivel():
    ant = Contador(AGORA, 10.0)
    assert energia_intervalo_wh(ant, AGORA + timedelta(seconds=5), 12.0, 360, 5) == pytest.approx(0.5)


def test_contador_cobre_lacuna_de_rede():
    # 10 min sem leituras a 1,2 kW: o contador registra os 200 Wh do período.
    ant = Contador(AGORA, 10.0)
    assert energia_intervalo_wh(ant, AGORA + timedelta(minutes=10), 10.2, 1200, 5) == pytest.approx(200)
