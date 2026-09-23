"""Análises descritivas sobre a série armazenada (tudo em SQL sobre as agregações contínuas)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg import Connection


def tarifa(conn: Connection) -> float:
    r = conn.execute("SELECT valor FROM configuracao WHERE chave = 'tarifa_kwh'").fetchone()
    return float(r["valor"]) if r else 0.0


def inicio_do_mes(agora: datetime, fuso: ZoneInfo) -> datetime:
    local = agora.astimezone(fuso)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def escolher_resolucao(inicio: datetime, fim: datetime) -> str:
    duracao = fim - inicio
    if duracao <= timedelta(hours=6):
        return "bruta"
    if duracao <= timedelta(days=3):
        return "1min"
    if duracao <= timedelta(days=62):
        return "1h"
    return "1d"


_SERIES = {
    "bruta": "SELECT ts, potencia_w, energia_wh, fp FROM leituras"
             " WHERE circuito_id = %(c)s AND ts >= %(i)s AND ts < %(f)s ORDER BY ts",
    "1min": "SELECT bucket AS ts, potencia_media_w AS potencia_w, energia_wh,"
            " soma_p / nullif(soma_s, 0) AS fp FROM leituras_1min"
            " WHERE circuito_id = %(c)s AND bucket >= %(i)s AND bucket < %(f)s ORDER BY bucket",
    # Em 1 hora, Wh consumidos equivalem numericamente à potência média em W.
    "1h": "SELECT bucket AS ts, energia_wh AS potencia_w, energia_wh,"
          " soma_p / nullif(soma_s, 0) AS fp FROM leituras_1h"
          " WHERE circuito_id = %(c)s AND bucket >= %(i)s AND bucket < %(f)s ORDER BY bucket",
    "1d": "SELECT bucket AS ts, energia_wh / 24 AS potencia_w, energia_wh,"
          " soma_p / nullif(soma_s, 0) AS fp FROM leituras_1d"
          " WHERE circuito_id = %(c)s AND bucket >= %(i)s AND bucket < %(f)s ORDER BY bucket",
}


def serie(conn: Connection, circuito: str, inicio: datetime, fim: datetime,
          resolucao: str | None = None) -> dict:
    resolucao = resolucao or escolher_resolucao(inicio, fim)
    pontos = conn.execute(_SERIES[resolucao], {"c": circuito, "i": inicio, "f": fim}).fetchall()
    return {"circuito": circuito, "resolucao": resolucao, "pontos": pontos}


def agora(conn: Connection) -> list[dict]:
    """Última leitura de cada circuito ativo nos últimos 10 minutos."""
    return conn.execute(
        """
        SELECT c.id, c.nome, c.no_id, c.configurado, c.disjuntor_a, c.fp_min, c.standby_w,
               n.simulado, n.status AS no_status,
               u.ts, u.tensao_v, u.corrente_a, u.potencia_w, u.fp, u.frequencia_hz,
               (SELECT count(*) FROM alertas a
                 WHERE a.circuito_id = c.id AND a.resolvido_em IS NULL) AS alertas_abertos
          FROM circuitos c
          JOIN nos n ON n.id = c.no_id
          LEFT JOIN LATERAL (
                SELECT * FROM leituras l
                 WHERE l.circuito_id = c.id AND l.ts > now() - INTERVAL '10 minutes'
                 ORDER BY l.ts DESC LIMIT 1) u ON true
         WHERE c.ativo
         ORDER BY c.nome
        """
    ).fetchall()


def ranking(conn: Connection, inicio: datetime, fim: datetime) -> dict:
    linhas = conn.execute(
        """
        SELECT c.id, c.nome, coalesce(sum(h.energia_wh), 0) / 1000 AS kwh
          FROM circuitos c
          LEFT JOIN leituras_1h h
                 ON h.circuito_id = c.id AND h.bucket >= %s AND h.bucket < %s
         WHERE c.ativo
         GROUP BY c.id, c.nome
         ORDER BY kwh DESC
        """,
        (inicio, fim),
    ).fetchall()
    total = sum(r["kwh"] for r in linhas)
    preco = tarifa(conn)
    for r in linhas:
        r["participacao"] = r["kwh"] / total if total else 0.0
        r["custo"] = r["kwh"] * preco
    return {"inicio": inicio, "fim": fim, "total_kwh": total, "total_custo": total * preco,
            "circuitos": linhas}


def perfil(conn: Connection, fuso: ZoneInfo, dias: int = 28, circuito: str | None = None) -> dict:
    """Potência média (kW) por dia da semana e hora, somando os circuitos escolhidos."""
    linhas = conn.execute(
        """
        WITH por_hora AS (
            SELECT bucket, sum(energia_wh) / 1000 AS kw
              FROM leituras_1h
             WHERE bucket >= now() - make_interval(days => %(dias)s)
               AND (%(c)s::text IS NULL OR circuito_id = %(c)s)
             GROUP BY bucket)
        SELECT extract(isodow FROM bucket AT TIME ZONE %(fuso)s)::int AS dia_semana,
               extract(hour FROM bucket AT TIME ZONE %(fuso)s)::int AS hora,
               avg(kw) AS kw_medio
          FROM por_hora
         GROUP BY 1, 2
         ORDER BY 1, 2
        """,
        {"dias": dias, "c": circuito, "fuso": str(fuso)},
    ).fetchall()
    return {"dias": dias, "circuito": circuito, "celulas": linhas}


def comparacao(conn: Connection, fuso: ZoneInfo, periodo: str, agora_: datetime) -> dict:
    """Consumo do período corrente até agora contra o mesmo trecho do período anterior."""
    local = agora_.astimezone(fuso)
    meia_noite = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if periodo == "dia":
        inicio, desloc = meia_noite, timedelta(days=1)
    elif periodo == "semana":
        inicio, desloc = meia_noite - timedelta(days=local.weekday()), timedelta(days=7)
    elif periodo == "mes":
        inicio = inicio_do_mes(agora_, fuso)
        desloc = inicio - (inicio - timedelta(days=1)).replace(day=1)
    else:
        raise ValueError("período deve ser dia, semana ou mes")

    linhas = conn.execute(
        """
        SELECT c.id, c.nome,
               coalesce(sum(m.energia_wh) FILTER (WHERE m.bucket >= %(i)s), 0) / 1000 AS atual_kwh,
               coalesce(sum(m.energia_wh) FILTER (WHERE m.bucket < %(fa)s), 0) / 1000
                   AS anterior_kwh
          FROM circuitos c
          LEFT JOIN leituras_1min m ON m.circuito_id = c.id
               AND ((m.bucket >= %(i)s AND m.bucket < %(f)s)
                 OR (m.bucket >= %(ia)s AND m.bucket < %(fa)s))
         WHERE c.ativo
         GROUP BY c.id, c.nome
         ORDER BY atual_kwh DESC
        """,
        {"i": inicio, "f": agora_, "ia": inicio - desloc, "fa": agora_ - desloc},
    ).fetchall()
    for r in linhas:
        r["variacao"] = ((r["atual_kwh"] - r["anterior_kwh"]) / r["anterior_kwh"]
                         if r["anterior_kwh"] else None)
    atual = sum(r["atual_kwh"] for r in linhas)
    anterior = sum(r["anterior_kwh"] for r in linhas)
    return {
        "periodo": periodo, "inicio": inicio, "inicio_anterior": inicio - desloc,
        "atual_kwh": atual, "anterior_kwh": anterior,
        "variacao": (atual - anterior) / anterior if anterior else None,
        "circuitos": linhas,
    }


def acumulado_mes(conn: Connection, fuso: ZoneInfo, agora_: datetime) -> dict:
    """Energia acumulada dia a dia no mês contra a média diária dos 90 dias anteriores."""
    inicio = inicio_do_mes(agora_, fuso)
    dias = conn.execute(
        """
        SELECT (bucket AT TIME ZONE %(fuso)s)::date AS dia, sum(energia_wh) / 1000 AS kwh
          FROM leituras_1d
         WHERE bucket >= %(i)s AND bucket < %(f)s
         GROUP BY 1 ORDER BY 1
        """,
        {"fuso": str(fuso), "i": inicio, "f": agora_},
    ).fetchall()
    ref = conn.execute(
        """
        SELECT avg(kwh) AS media FROM (
            SELECT bucket, sum(energia_wh) / 1000 AS kwh FROM leituras_1d
             WHERE bucket >= %s AND bucket < %s GROUP BY bucket) d
        """,
        (inicio - timedelta(days=90), inicio),
    ).fetchone()
    media = ref["media"]
    acumulado = 0.0
    pontos = []
    for i, d in enumerate(dias, start=1):
        acumulado += d["kwh"]
        pontos.append({"dia": d["dia"], "kwh": d["kwh"], "acumulado_kwh": acumulado,
                       "referencia_kwh": media * i if media is not None else None})
    return {"inicio": inicio, "media_diaria_referencia_kwh": media, "pontos": pontos}


def fora_de_janela(conn: Connection, fuso: ZoneInfo, inicio: datetime, fim: datetime) -> dict:
    linhas = conn.execute(
        """
        SELECT c.id, c.nome, c.janela_inicio, c.janela_fim,
               coalesce(sum(m.energia_wh), 0) / 1000 AS total_kwh,
               coalesce(sum(m.energia_wh) FILTER (WHERE NOT dentro_janela(
                   m.bucket, c.janela_inicio, c.janela_fim, c.dias_semana, %(fuso)s)), 0) / 1000
                   AS fora_kwh
          FROM circuitos c
          LEFT JOIN leituras_1min m ON m.circuito_id = c.id
               AND m.bucket >= %(i)s AND m.bucket < %(f)s
         WHERE c.ativo AND c.janela_inicio IS NOT NULL
         GROUP BY c.id, c.nome, c.janela_inicio, c.janela_fim
         ORDER BY fora_kwh DESC
        """,
        {"fuso": str(fuso), "i": inicio, "f": fim},
    ).fetchall()
    preco = tarifa(conn)
    for r in linhas:
        r["fracao"] = r["fora_kwh"] / r["total_kwh"] if r["total_kwh"] else 0.0
        r["custo_fora"] = r["fora_kwh"] * preco
    return {"inicio": inicio, "fim": fim, "circuitos": linhas}


def projecao(conn: Connection, fuso: ZoneInfo, agora_: datetime) -> dict:
    """Projeção do mês: consumido até agora + média dos últimos 7 dias × tempo restante."""
    inicio = inicio_do_mes(agora_, fuso)
    fim_mes = (inicio + timedelta(days=32)).replace(day=1)
    r = conn.execute(
        """
        SELECT coalesce(sum(energia_wh) FILTER (WHERE bucket >= %(i)s), 0) / 1000 AS mes_kwh,
               coalesce(sum(energia_wh) FILTER (WHERE bucket >= %(s)s), 0) / 1000 AS semana_kwh,
               min(bucket) FILTER (WHERE bucket >= %(s)s) AS primeiro
          FROM leituras_1h
         WHERE bucket >= least(%(i)s, %(s)s)
        """,
        {"i": inicio, "s": agora_ - timedelta(days=7)},
    ).fetchone()
    dias_base = (agora_ - r["primeiro"]).total_seconds() / 86400 if r["primeiro"] else 0
    media_dia = r["semana_kwh"] / dias_base if dias_base >= 1 else None
    restante = (fim_mes - agora_.astimezone(fuso)).total_seconds() / 86400
    projetado = r["mes_kwh"] + media_dia * restante if media_dia is not None else None
    preco = tarifa(conn)
    return {
        "inicio_mes": inicio, "fim_mes": fim_mes, "tarifa_kwh": preco,
        "consumido_kwh": r["mes_kwh"], "consumido_custo": r["mes_kwh"] * preco,
        "media_diaria_kwh": media_dia,
        "projetado_kwh": projetado,
        "projetado_custo": projetado * preco if projetado is not None else None,
    }
