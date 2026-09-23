"""Simulador de nó sensor para desenvolvimento e demonstração.

Publica no mesmo tópico e formato do firmware, sob o nó "sim-01", que fica marcado como
simulado no banco e no painel: dado simulado nunca se confunde com medição real.

    energia-simulador --historico 45            # grava 45 dias de histórico direto no banco
    energia-simulador --tempo-real              # publica leituras a cada 5 s via MQTT
    energia-simulador --tempo-real --anomalia bomba_travada
"""

import argparse
import json
import logging
import random
import signal
import threading
import time as relogio
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt
import psycopg

from energia.config import config
from energia.db import migrar

log = logging.getLogger("energia.simulador")

NO = "sim-01"
ANOMALIAS = ("bomba_travada", "fp_baixo", "sobrecorrente", "salao_esquecido")


@dataclass(frozen=True)
class Perfil:
    canal: str
    nome: str
    tensao: float
    disjuntor_a: float
    standby_w: float
    fp: float
    # Motores têm FP naturalmente menor; o mínimo aceitável é por circuito.
    fp_min: float = 0.92
    janela: tuple[time, time] | None = None
    max_continuo_min: int | None = None


# Cargas típicas das áreas comuns relatadas pelo síndico no Plano de Ação.
PERFIS = [
    Perfil("c1", "Bombas de recalque", 220, 20, 50, 0.85, fp_min=0.8, max_continuo_min=40),
    Perfil("c2", "Iluminação da garagem", 127, 16, 50, 0.96),
    Perfil("c3", "Iluminação de halls e escadas", 127, 16, 60, 0.96,
           janela=(time(17, 30), time(7, 0))),
    Perfil("c4", "Portões automáticos", 127, 10, 40, 0.78, fp_min=0.7),
    Perfil("c5", "Casa de máquinas (elevadores)", 220, 32, 300, 0.83, fp_min=0.75),
    Perfil("c6", "Salão de festas", 127, 20, 80, 0.97, janela=(time(8, 0), time(23, 0))),
]


def _bomba(t: datetime, travada: bool) -> float:
    if travada:
        return 1900
    minuto = t.hour * 60 + t.minute
    pico = 6 <= t.hour < 9 or 18 <= t.hour < 22
    periodo, ligada = (35, 12) if pico else (90, 10)
    return 1900 if minuto % periodo < ligada else 0


def potencia(p: Perfil, t: datetime, rng: random.Random, anomalias: set[str]) -> float:
    """Potência ativa (W) do circuito no instante t (hora local)."""
    h = t.hour + t.minute / 60
    fim_de_semana = t.isoweekday() >= 6
    match p.canal:
        case "c1":
            w = _bomba(t, "bomba_travada" in anomalias)
        case "c2":
            w = 720 if (h >= 18 or h < 6.5) else 220
        case "c3":
            noite = h >= 17.75 or h < 6.75
            # sensores de presença: a iluminação cheia acende só parte do tempo
            w = (450 if rng.random() < 0.55 else 180) if noite else 25
        case "c4":
            movimento = 0.12 if 6 <= h < 22 else 0.02
            w = 620 if rng.random() < movimento else 15
        case "c5":
            viagem = 0.25 if 6 <= h < 23 else 0.04
            w = rng.uniform(2800, 3600) if rng.random() < viagem else 180
        case "c6":
            evento = fim_de_semana and 14 <= h < 23
            w = 1650 if evento or "salao_esquecido" in anomalias else 0
            w += 150 if rng.random() < 0.4 else 40  # geladeira
        case _:
            w = 0.0
    if "sobrecorrente" in anomalias and p.canal == "c5":
        w = 6000
    return max(0.0, w * rng.uniform(0.96, 1.04))


def leitura(p: Perfil, t: datetime, rng: random.Random, anomalias: set[str]) -> dict:
    w = potencia(p, t, rng, anomalias)
    fp = p.fp
    if "fp_baixo" in anomalias and p.canal == "c3":
        fp = 0.58  # reatores degradados
    fp = min(1.0, max(0.3, fp + rng.uniform(-0.02, 0.02)))
    v = p.tensao * rng.uniform(0.97, 1.03)
    i = w / (v * fp) if w > 0 else 0.0
    return {"id": p.canal, "v": round(v, 1), "i": round(i, 3), "p": round(w, 1),
            "fp": round(fp, 2) if w > 0 else None, "f": round(rng.uniform(59.9, 60.1), 1)}


def registrar_circuitos(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO nos (id, descricao, simulado) VALUES (%s, %s, true)"
        " ON CONFLICT (id) DO UPDATE SET simulado = true",
        (NO, "Nó simulado para desenvolvimento e demonstração"),
    )
    for p in PERFIS:
        conn.execute(
            "INSERT INTO circuitos (id, no_id, canal, nome, disjuntor_a, fp_min, standby_w,"
            " janela_inicio, janela_fim, max_continuo_min, configurado)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true) ON CONFLICT (id) DO NOTHING",
            (f"{NO}.{p.canal}", NO, p.canal, p.nome, p.disjuntor_a, p.fp_min, p.standby_w,
             p.janela[0] if p.janela else None, p.janela[1] if p.janela else None,
             p.max_continuo_min),
        )


def gerar_historico(conn: psycopg.Connection, dias: int, fuso: ZoneInfo, semente: int) -> int:
    """Uma leitura por minuto e circuito, com alguns eventos anômalos pelo caminho."""
    rng = random.Random(semente)
    fim = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=2)
    inicio = fim - timedelta(days=dias)
    # Eventos para as análises terem o que mostrar (sempre dentro do histórico simulado).
    falha_boia = (fim - timedelta(days=min(dias - 1, 9), hours=4), timedelta(hours=3))
    salao_noite = (fim - timedelta(days=min(dias - 1, 4), hours=8), timedelta(hours=7))

    ja_existe = conn.execute(
        "SELECT max(ts) AS m FROM leituras WHERE circuito_id LIKE %s", (f"{NO}.%",)
    ).fetchone()[0]
    if ja_existe:
        inicio = max(inicio, ja_existe + timedelta(minutes=1))
    contadores = _contadores_iniciais(conn)
    total = 0
    with conn.cursor().copy(
        "COPY leituras (ts, circuito_id, seq, tensao_v, corrente_a, potencia_w, fp,"
        " frequencia_hz, energia_total_kwh, energia_wh) FROM STDIN"
    ) as copia:
        t = inicio
        seq = 0
        while t <= fim:
            local = t.astimezone(fuso)
            anom = set()
            if falha_boia[0] <= t < falha_boia[0] + falha_boia[1]:
                anom.add("bomba_travada")
            if salao_noite[0] <= t < salao_noite[0] + salao_noite[1]:
                anom.add("salao_esquecido")
            seq += 1
            for p in PERFIS:
                r = leitura(p, local, rng, anom)
                wh = r["p"] / 60
                contadores[p.canal] += wh / 1000
                copia.write_row((t, f"{NO}.{p.canal}", seq, r["v"], r["i"], r["p"], r["fp"],
                                 r["f"], contadores[p.canal], wh))
                total += 1
            t += timedelta(minutes=1)
    return total


def atualizar_agregacoes(url: str) -> None:
    # refresh_continuous_aggregate não roda dentro de transação: conexão em autocommit.
    with psycopg.connect(url, autocommit=True) as conn:
        for visao in ("leituras_1min", "leituras_1h", "leituras_1d"):
            conn.execute(f"CALL refresh_continuous_aggregate('{visao}', NULL, now())")


def tempo_real(intervalo_s: float, anomalias: set[str], fuso: ZoneInfo, semente: int) -> None:
    cfg = config()
    rng = random.Random(semente)
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=NO)
    if cfg.mqtt_usuario:
        cliente.username_pw_set(cfg.mqtt_usuario, cfg.mqtt_senha)
    topico_status = f"{cfg.mqtt_prefixo}/{NO}/status"
    cliente.will_set(topico_status, "offline", qos=1, retain=True)
    cliente.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=30)
    cliente.loop_start()
    cliente.publish(topico_status, "online", qos=1, retain=True)

    try:
        with psycopg.connect(cfg.database_url) as conn:
            contadores = _contadores_iniciais(conn)
    except psycopg.Error as e:
        log.warning("sem banco para retomar os contadores (%s); começando do zero", e)
        contadores = dict.fromkeys((p.canal for p in PERFIS), 0.0)
    parar = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    signal.signal(signal.SIGINT, lambda *_: parar.set())
    seq = int(relogio.time())
    log.info("publicando a cada %.0f s; anomalias: %s", intervalo_s, ", ".join(anomalias) or "-")
    while not parar.is_set():
        agora = datetime.now(UTC)
        seq += 1
        circuitos = []
        for p in PERFIS:
            r = leitura(p, agora.astimezone(fuso), rng, anomalias)
            contadores[p.canal] += r["p"] * intervalo_s / 3_600_000
            r["e_kwh"] = round(contadores[p.canal], 4)
            circuitos.append(r)
        corpo = {"no": NO, "ts": int(agora.timestamp() * 1000), "seq": seq,
                 "intervalo_s": intervalo_s, "fw": "simulador", "circuitos": circuitos}
        cliente.publish(f"{cfg.mqtt_prefixo}/{NO}/leituras", json.dumps(corpo), qos=1)
        parar.wait(intervalo_s)
    cliente.publish(topico_status, "offline", qos=1, retain=True).wait_for_publish(5)
    cliente.loop_stop()
    cliente.disconnect()


def _contadores_iniciais(conn: psycopg.Connection) -> dict[str, float]:
    """Retoma o contador acumulado de onde parou, como faz o PZEM real."""
    contadores = dict.fromkeys((p.canal for p in PERFIS), 0.0)
    for cid, e in conn.execute(
        "SELECT DISTINCT ON (circuito_id) circuito_id, energia_total_kwh FROM leituras"
        " WHERE circuito_id LIKE %s ORDER BY circuito_id, ts DESC", (f"{NO}.%",)
    ):
        contadores[cid.split(".", 1)[1]] = e or 0.0
    return contadores


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--historico", type=int, default=0, metavar="DIAS",
                    help="grava DIAS de histórico simulado direto no banco")
    ap.add_argument("--tempo-real", action="store_true", help="publica leituras via MQTT")
    ap.add_argument("--intervalo", type=float, default=5.0, help="segundos entre leituras")
    ap.add_argument("--anomalia", action="append", default=[], choices=ANOMALIAS)
    ap.add_argument("--semente", type=int, default=2026)
    args = ap.parse_args()
    if not args.historico and not args.tempo_real:
        ap.error("use --historico DIAS e/ou --tempo-real")

    cfg = config()
    fuso = ZoneInfo(cfg.fuso)
    migrar()
    with psycopg.connect(cfg.database_url) as conn:
        registrar_circuitos(conn)
        if args.historico:
            log.info("gerando %d dias de histórico simulado...", args.historico)
            n = gerar_historico(conn, args.historico, fuso, args.semente)
            conn.commit()
            log.info("%d leituras gravadas", n)
    if args.historico:
        atualizar_agregacoes(cfg.database_url)
        log.info("agregações atualizadas")
    if args.tempo_real:
        tempo_real(args.intervalo, set(args.anomalia), fuso, args.semente)


if __name__ == "__main__":
    main()
