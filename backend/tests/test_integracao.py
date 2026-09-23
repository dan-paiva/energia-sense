"""Testes contra um TimescaleDB de verdade.

Rodam só com TEST_DATABASE_URL definida (a CI sobe o banco como serviço). O banco apontado
é apagado e recriado a cada execução.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from energia import motor
from energia.config import config
from energia.db import migrar
from energia.ingestao import Gravador
from energia.payload import interpretar

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definida")
SP = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(scope="module")
def url():
    partes = urlsplit(URL)
    nome = partes.path.lstrip("/")
    admin = urlunsplit(partes._replace(path="/postgres"))
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{nome}"')
    assert migrar(URL)
    return URL


@pytest.fixture
def conn(url):
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
        yield c


def msg(no: str, ts: datetime, circuitos: list[dict], seq: int = 1):
    corpo = {"no": no, "ts": int(ts.timestamp() * 1000), "seq": seq, "intervalo_s": 5,
             "circuitos": circuitos}
    return interpretar(json.dumps(corpo).encode()), ts


def test_migracao_e_idempotente(url):
    assert migrar(url) == []


def test_gravador_registra_no_circuito_e_energia(conn):
    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=30)
    g = Gravador()
    g.gravar(conn, [
        msg("no-t1", t0, [{"id": "c1", "v": 127, "i": 5.9, "p": 720, "fp": 0.96, "e_kwh": 3.0}]),
        msg("no-t1", t0 + timedelta(seconds=5),
            [{"id": "c1", "v": 127, "i": 5.9, "p": 720, "fp": 0.96, "e_kwh": 3.001}], seq=2),
    ])
    c = conn.execute("SELECT * FROM circuitos WHERE id = 'no-t1.c1'").fetchone()
    assert c["configurado"] is False and c["no_id"] == "no-t1"
    energias = [r["energia_wh"] for r in conn.execute(
        "SELECT energia_wh FROM leituras WHERE circuito_id = 'no-t1.c1' ORDER BY ts")]
    assert energias == pytest.approx([1.0, 1.0])
    assert conn.execute("SELECT status FROM nos WHERE id = 'no-t1'").fetchone()["status"] == "online"


def test_reenvio_do_buffer_nao_duplica(conn):
    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=20)
    leitura = [{"id": "c1", "i": 1, "p": 100, "e_kwh": 1.0}]
    Gravador().gravar(conn, [msg("no-t2", t0, leitura)])
    Gravador().gravar(conn, [msg("no-t2", t0, leitura)])
    n = conn.execute("SELECT count(*) AS n FROM leituras WHERE circuito_id = 'no-t2.c1'")
    assert n.fetchone()["n"] == 1


def test_motor_abre_e_resolve_alerta_de_bomba_travada(conn):
    conn.execute("INSERT INTO nos (id) VALUES ('no-t3')")
    conn.execute(
        "INSERT INTO circuitos (id, no_id, canal, nome, standby_w, max_continuo_min, configurado)"
        " VALUES ('no-t3.c1', 'no-t3', 'c1', 'Bomba teste', 50, 40, true)"
    )
    agora = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=3)
    ligada = [msg("no-t3", agora - timedelta(seconds=5 * k),
                  [{"id": "c1", "i": 10, "p": 1900, "fp": 0.95}], seq=k) for k in range(45 * 12, -1, -1)]
    Gravador().gravar(conn, ligada)

    novos, _ = motor.sincronizar(conn, motor.condicoes_ativas(conn, agora, SP), agora)
    assert [(a["tipo"], a["circuito_id"]) for a in novos] == [("operacao_continua", "no-t3.c1")]

    # Nova avaliação com a condição ainda presente não duplica o alerta.
    novos, _ = motor.sincronizar(conn, motor.condicoes_ativas(conn, agora, SP), agora)
    assert novos == []

    depois = agora + timedelta(minutes=45)
    desligada = [msg("no-t3", depois - timedelta(seconds=5 * k),
                     [{"id": "c1", "i": 0, "p": 0}], seq=10_000 + k) for k in range(40 * 12, -1, -1)]
    Gravador().gravar(conn, desligada)
    _, resolvidos = motor.sincronizar(conn, motor.condicoes_ativas(conn, depois, SP), depois)
    assert [a["circuito_id"] for a in resolvidos if a["tipo"] == "operacao_continua"] == ["no-t3.c1"]


@pytest.fixture
def cliente(url, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", url)
    config.cache_clear()
    from energia.api import app

    with TestClient(app) as c:
        yield c
    config.cache_clear()


def test_api_de_ponta_a_ponta(cliente, conn):
    with psycopg.connect(URL, autocommit=True) as c:
        for v in ("leituras_1min", "leituras_1h", "leituras_1d"):
            c.execute(f"CALL refresh_continuous_aggregate('{v}', NULL, now())")

    assert cliente.get("/api/saude").json() == {"ok": True}
    agora = cliente.get("/api/agora").json()
    assert {c["id"] for c in agora["circuitos"]} >= {"no-t1.c1", "no-t3.c1"}

    r = cliente.put("/api/circuitos/no-t1.c1", json={
        "nome": "Garagem", "disjuntor_a": 16, "janela_inicio": "18:00", "janela_fim": "06:30",
    })
    assert r.status_code == 200 and r.json()["configurado"] is True
    r = cliente.put("/api/circuitos/no-t1.c1", json={"nome": "Garagem", "janela_inicio": "18:00"})
    assert r.status_code == 422

    for caminho in ("/analise/ranking", "/analise/perfil", "/analise/comparacao?periodo=mes",
                    "/analise/acumulado", "/analise/fora-de-janela", "/analise/projecao",
                    "/alertas", "/nos", "/configuracao",
                    "/series?circuito=no-t1.c1&resolucao=bruta"):
        assert cliente.get(f"/api{caminho}").status_code == 200, caminho

    ranking = cliente.get("/api/analise/ranking").json()
    assert ranking["total_kwh"] > 0

    csv = cliente.get("/api/exportar.csv")
    assert csv.status_code == 200 and csv.text.startswith("hora;circuito")

    r = cliente.put("/api/configuracao", json={"tarifa_kwh": 1.1})
    assert r.json()["tarifa_kwh"] == 1.1
    assert cliente.get("/").status_code == 200


def test_login_quando_senha_configurada(cliente, monkeypatch):
    monkeypatch.setenv("PAINEL_SENHA", "segredo")
    config.cache_clear()
    assert cliente.get("/api/agora").status_code == 401
    assert cliente.get("/").status_code == 401
    assert cliente.get("/api/saude").status_code == 200
    assert cliente.get("/api/agora", auth=("sindico", "segredo")).status_code == 200
    assert cliente.get("/api/agora", auth=("sindico", "errada")).status_code == 401
