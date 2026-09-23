"""Motor de alertas: avalia as regras sobre as leituras recentes e sincroniza a tabela alertas."""

import logging
import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg import Connection
from psycopg_pool import ConnectionPool

from energia.notificacao import Notificador
from energia.regras import (
    Amostra,
    Circuito,
    Condicao,
    Parametros,
    avaliar_circuito,
    avaliar_no,
)

log = logging.getLogger(__name__)


def carregar_parametros(conn: Connection) -> Parametros:
    cfg = {r["chave"]: r["valor"] for r in conn.execute("SELECT chave, valor FROM configuracao")}
    padrao = Parametros()
    return Parametros(
        pct_disjuntor=float(cfg.get("pct_disjuntor", padrao.pct_disjuntor)),
        janela_fp_min=int(cfg.get("janela_fp_min", padrao.janela_fp_min)),
        min_fora_janela=int(cfg.get("min_fora_janela", padrao.min_fora_janela)),
        no_offline_s=int(cfg.get("no_offline_s", padrao.no_offline_s)),
    )


def carregar_circuitos(conn: Connection) -> list[Circuito]:
    linhas = conn.execute(
        "SELECT id, nome, no_id, disjuntor_a, fp_min, standby_w, janela_inicio, janela_fim,"
        " dias_semana, max_continuo_min FROM circuitos WHERE ativo"
    ).fetchall()
    return [
        Circuito(
            id=r["id"], nome=r["nome"], no_id=r["no_id"], disjuntor_a=r["disjuntor_a"],
            fp_min=r["fp_min"], standby_w=r["standby_w"], janela_inicio=r["janela_inicio"],
            janela_fim=r["janela_fim"], dias_semana=tuple(r["dias_semana"]),
            max_continuo_min=r["max_continuo_min"],
        )
        for r in linhas
    ]


def condicoes_ativas(conn: Connection, agora: datetime, fuso: ZoneInfo) -> list[Condicao]:
    p = carregar_parametros(conn)
    circuitos = carregar_circuitos(conn)
    if not circuitos:
        return []

    olhar_min = max([1, p.janela_fp_min, p.min_fora_janela]
                    + [c.max_continuo_min or 0 for c in circuitos]) + 1
    por_circuito: dict[str, list[Amostra]] = {c.id: [] for c in circuitos}
    for r in conn.execute(
        "SELECT circuito_id, ts, potencia_w, corrente_a, fp FROM leituras"
        " WHERE ts >= %s AND ts <= %s AND circuito_id = ANY(%s) ORDER BY ts",
        (agora - timedelta(minutes=olhar_min), agora, list(por_circuito)),
    ):
        por_circuito[r["circuito_id"]].append(
            Amostra(r["ts"].astimezone(fuso), r["potencia_w"], r["corrente_a"], r["fp"])
        )

    agora_local = agora.astimezone(fuso)
    ativas: list[Condicao] = []
    for c in circuitos:
        ativas.extend(avaliar_circuito(c, por_circuito[c.id], agora_local, p))

    nos_monitorados = {c.no_id for c in circuitos}
    for r in conn.execute("SELECT id, ultimo_contato FROM nos"):
        if r["id"] in nos_monitorados:
            cond = avaliar_no(r["id"], r["ultimo_contato"], agora, p)
            if cond:
                ativas.append(cond)
    return ativas


def sincronizar(
    conn: Connection, ativas: list[Condicao], agora: datetime
) -> tuple[list[dict], list[dict]]:
    """Abre alertas novos, atualiza os que continuam e resolve os que sumiram."""
    abertos = {
        (r["tipo"], r["circuito_id"] or "", r["no_id"] or ""): r["id"]
        for r in conn.execute(
            "SELECT id, tipo, circuito_id, no_id FROM alertas WHERE resolvido_em IS NULL"
        )
    }
    novos: list[dict] = []
    resolvidos: list[dict] = []
    with conn.transaction():
        for c in ativas:
            if c.chave in abertos:
                conn.execute(
                    "UPDATE alertas SET valor = %s, mensagem = %s, atualizado_em = %s"
                    " WHERE id = %s",
                    (c.valor, c.mensagem, agora, abertos.pop(c.chave)),
                )
                continue
            linha = conn.execute(
                "INSERT INTO alertas (tipo, severidade, circuito_id, no_id, mensagem, valor,"
                " limite, aberto_em, atualizado_em) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT DO NOTHING RETURNING *",
                (c.tipo, c.severidade, c.circuito_id, c.no_id, c.mensagem, c.valor, c.limite,
                 agora, agora),
            ).fetchone()
            if linha:
                novos.append(linha)
        for id_ in abertos.values():
            linha = conn.execute(
                "UPDATE alertas SET resolvido_em = %s, atualizado_em = %s WHERE id = %s"
                " RETURNING *",
                (agora, agora, id_),
            ).fetchone()
            if linha:
                resolvidos.append(linha)
    return novos, resolvidos


def ciclo(
    pool: ConnectionPool, notificador: Notificador, fuso: ZoneInfo, agora: datetime | None = None
) -> tuple[int, int]:
    agora = agora or datetime.now(UTC)
    with pool.connection() as conn:
        novos, resolvidos = sincronizar(conn, condicoes_ativas(conn, agora, fuso), agora)
    for a in novos:
        notificador.enviar("aberto", _serializavel(a))
    for a in resolvidos:
        notificador.enviar("resolvido", _serializavel(a))
    return len(novos), len(resolvidos)


def _serializavel(alerta: dict) -> dict:
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in alerta.items()}


def iniciar_em_thread(
    pool: ConnectionPool, notificador: Notificador, fuso: ZoneInfo, intervalo_s: int,
    parar: threading.Event,
) -> threading.Thread:
    def laco() -> None:
        while not parar.wait(intervalo_s):
            try:
                novos, resolvidos = ciclo(pool, notificador, fuso)
                if novos or resolvidos:
                    log.info("alertas: %d novos, %d resolvidos", novos, resolvidos)
            except Exception:
                log.exception("falha no ciclo do motor de alertas")

    t = threading.Thread(target=laco, name="motor-alertas", daemon=True)
    t.start()
    return t
