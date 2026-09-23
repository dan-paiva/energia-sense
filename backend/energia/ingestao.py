"""Serviço de ingestão: assina o broker MQTT, valida, calcula energia e grava em lote.

Também roda o motor de alertas numa thread própria.
"""

import logging
import queue
import signal
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt
from psycopg import Connection
from psycopg_pool import ConnectionPool

from energia import motor
from energia.config import Config, config
from energia.db import criar_pool, migrar
from energia.notificacao import Notificador
from energia.payload import (
    Contador,
    Mensagem,
    PayloadInvalido,
    energia_intervalo_wh,
    interpretar,
    resolver_ts,
)

log = logging.getLogger("energia.ingestao")

LOTE_MAX = 500
LOTE_ESPERA_S = 1.0


@dataclass
class Recebida:
    topico: str
    payload: bytes
    recebido_em: datetime


class Gravador:
    """Transforma mensagens em linhas de leitura. Mantém o último contador por circuito."""

    def __init__(self) -> None:
        self.contadores: dict[str, Contador] = {}
        self.circuitos: set[str] = set()

    def gravar(self, conn: Connection, msgs: list[tuple[Mensagem, datetime]]) -> int:
        linhas = []
        with conn.transaction():
            for msg, recebido_em in msgs:
                ts = resolver_ts(msg.ts, recebido_em)
                self._registrar_no(conn, msg, recebido_em)
                for c in msg.circuitos:
                    cid = f"{msg.no}.{c.id}"
                    self._garantir_circuito(conn, cid, msg.no, c.id)
                    anterior = self._contador(conn, cid)
                    energia = energia_intervalo_wh(anterior, ts, c.e_kwh, c.p, msg.intervalo_s)
                    if anterior is None or ts > anterior.ts:
                        self.contadores[cid] = Contador(ts, c.e_kwh)
                    linhas.append((ts, cid, msg.seq, c.v, c.i, c.p, c.fp, c.f, c.e_kwh, energia))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO leituras (ts, circuito_id, seq, tensao_v, corrente_a, potencia_w,"
                    " fp, frequencia_hz, energia_total_kwh, energia_wh)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    linhas,
                )
        return len(linhas)

    def _registrar_no(self, conn: Connection, msg: Mensagem, recebido_em: datetime) -> None:
        conn.execute(
            "INSERT INTO nos (id, status, firmware, ultimo_contato) VALUES (%s, 'online', %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET status = 'online',"
            " firmware = coalesce(EXCLUDED.firmware, nos.firmware),"
            " ultimo_contato = greatest(nos.ultimo_contato, EXCLUDED.ultimo_contato)",
            (msg.no, msg.fw, recebido_em),
        )

    def _garantir_circuito(self, conn: Connection, cid: str, no: str, canal: str) -> None:
        # Canal novo entra ativo e sem configuração; o síndico dá nome e limites no painel.
        if cid in self.circuitos:
            return
        conn.execute(
            "INSERT INTO circuitos (id, no_id, canal, nome) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (id) DO NOTHING",
            (cid, no, canal, f"Circuito {canal} ({no})"),
        )
        self.circuitos.add(cid)

    def _contador(self, conn: Connection, cid: str) -> Contador | None:
        if cid not in self.contadores:
            r = conn.execute(
                "SELECT ts, energia_total_kwh FROM leituras WHERE circuito_id = %s"
                " ORDER BY ts DESC LIMIT 1",
                (cid,),
            ).fetchone()
            if r is None:
                return None
            self.contadores[cid] = Contador(r["ts"], r["energia_total_kwh"])
        return self.contadores[cid]


def atualizar_status(conn: Connection, no: str, status: str) -> None:
    conn.execute(
        "INSERT INTO nos (id, status) VALUES (%s, %s)"
        " ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status",
        (no, status),
    )


def processar_lote(pool: ConnectionPool, gravador: Gravador, lote: list[Recebida]) -> int:
    msgs: list[tuple[Mensagem, datetime]] = []
    with pool.connection() as conn:
        for r in lote:
            partes = r.topico.split("/")
            if len(partes) != 3:
                continue
            _, no, tipo = partes
            if tipo == "status":
                status = r.payload.decode("utf-8", "replace").strip().lower()
                if status in ("online", "offline"):
                    atualizar_status(conn, no, status)
                continue
            try:
                msg = interpretar(r.payload)
            except PayloadInvalido as e:
                log.warning("payload inválido em %s: %s", r.topico, e)
                continue
            if msg.no != no:
                log.warning("nó do payload (%s) difere do tópico (%s); descartado", msg.no, no)
                continue
            msgs.append((msg, r.recebido_em))
        return gravador.gravar(conn, msgs) if msgs else 0


def criar_cliente(cfg: Config, fila: "queue.Queue[Recebida]") -> mqtt.Client:
    # Sessão persistente + QoS 1: o broker guarda as mensagens se a ingestão reiniciar.
    cliente = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id="energia-ingestao", clean_session=False
    )
    if cfg.mqtt_usuario:
        cliente.username_pw_set(cfg.mqtt_usuario, cfg.mqtt_senha)
    cliente.reconnect_delay_set(min_delay=1, max_delay=30)

    def ao_conectar(cli, _userdata, _flags, reason_code, _props):
        if reason_code.is_failure:
            log.error("broker recusou a conexão: %s", reason_code)
            return
        log.info("conectado ao broker %s:%s", cfg.mqtt_host, cfg.mqtt_port)
        cli.subscribe([(f"{cfg.mqtt_prefixo}/+/leituras", 1), (f"{cfg.mqtt_prefixo}/+/status", 1)])

    def ao_receber(_cli, _userdata, m):
        fila.put(Recebida(m.topic, m.payload, datetime.now(UTC)))

    cliente.on_connect = ao_conectar
    cliente.on_message = ao_receber
    return cliente


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = config()
    migrar()
    pool = criar_pool(max_size=4)
    parar = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    signal.signal(signal.SIGINT, lambda *_: parar.set())

    motor.iniciar_em_thread(
        pool, Notificador(cfg), ZoneInfo(cfg.fuso), cfg.intervalo_regras_s, parar
    )

    fila: queue.Queue[Recebida] = queue.Queue(maxsize=50_000)
    cliente = criar_cliente(cfg, fila)
    cliente.connect_async(cfg.mqtt_host, cfg.mqtt_port, keepalive=30)
    cliente.loop_start()

    gravador = Gravador()
    while not parar.is_set():
        lote: list[Recebida] = []
        limite = time.monotonic() + LOTE_ESPERA_S
        while len(lote) < LOTE_MAX and (restante := limite - time.monotonic()) > 0:
            try:
                lote.append(fila.get(timeout=restante))
            except queue.Empty:
                break
        if not lote:
            continue
        try:
            n = processar_lote(pool, gravador, lote)
            log.debug("%d leituras gravadas", n)
        except Exception:
            # Descarta o cache: após rollback ele pode apontar para linhas não gravadas.
            gravador = Gravador()
            log.exception("falha ao gravar lote de %d mensagens", len(lote))

    cliente.loop_stop()
    cliente.disconnect()
    pool.close()


if __name__ == "__main__":
    main()
