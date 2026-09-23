"""API HTTP e painel web do EnergIA Sense."""

import base64
import csv
import io
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time, timedelta
from importlib import resources
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, model_validator

from energia import analise
from energia.config import config
from energia.db import criar_pool, migrar

log = logging.getLogger(__name__)
estado: dict = {}


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    migrar()
    estado["pool"] = criar_pool()
    yield
    estado["pool"].close()


app = FastAPI(title="EnergIA Sense", version="0.1.0", lifespan=ciclo_de_vida)


@app.middleware("http")
async def autenticar(request: Request, call_next):
    """Com PAINEL_SENHA definida, tudo (painel e API) pede login, menos a checagem de saúde."""
    cfg = config()
    if not cfg.painel_senha or request.url.path == "/api/saude":
        return await call_next(request)
    usuario, senha = "", ""
    cabecalho = request.headers.get("authorization", "")
    if cabecalho.lower().startswith("basic "):
        try:
            usuario, _, senha = base64.b64decode(cabecalho[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            pass
    ok = secrets.compare_digest(usuario.encode(), cfg.painel_usuario.encode()) & \
        secrets.compare_digest(senha.encode(), cfg.painel_senha.encode())
    if ok:
        return await call_next(request)
    return Response(
        "login necessário", status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="EnergIA Sense", charset="UTF-8"'},
    )


def conexao():
    with estado["pool"].connection() as conn:
        yield conn


Conn = Annotated[object, Depends(conexao)]


def fuso() -> ZoneInfo:
    return ZoneInfo(config().fuso)


def _periodo(inicio: datetime | None, fim: datetime | None, padrao: timedelta):
    fim = fim or datetime.now(UTC)
    inicio = inicio or fim - padrao
    if inicio >= fim:
        raise HTTPException(422, "inicio deve ser anterior a fim")
    return inicio, fim


@app.get("/api/saude")
def saude(conn: Conn):
    conn.execute("SELECT 1")
    return {"ok": True}


@app.get("/api/agora")
def ler_agora(conn: Conn):
    circuitos = analise.agora(conn)
    total = sum(c["potencia_w"] or 0 for c in circuitos)
    return {"ts": datetime.now(UTC), "potencia_total_w": total, "circuitos": circuitos}


@app.get("/api/series")
def series(
    conn: Conn, circuito: str,
    inicio: datetime | None = None, fim: datetime | None = None,
    resolucao: Literal["bruta", "1min", "1h", "1d"] | None = None,
):
    i, f = _periodo(inicio, fim, timedelta(hours=24))
    return analise.serie(conn, circuito, i, f, resolucao)


# ---- cadastro de circuitos e nós ------------------------------------------------------

class CircuitoEntrada(BaseModel):
    nome: str = Field(min_length=1, max_length=80)
    sensor: Literal["pzem", "sct"] = "pzem"
    disjuntor_a: float | None = Field(None, gt=0, le=400)
    fp_min: float = Field(0.92, ge=0, le=1)
    standby_w: float = Field(30, ge=0)
    janela_inicio: time | None = None
    janela_fim: time | None = None
    dias_semana: list[int] = Field(default=[1, 2, 3, 4, 5, 6, 7], min_length=1)
    max_continuo_min: int | None = Field(None, gt=0, le=24 * 60)
    ativo: bool = True

    @model_validator(mode="after")
    def validar(self):
        if (self.janela_inicio is None) != (self.janela_fim is None):
            raise ValueError("informe início e fim da janela, ou nenhum dos dois")
        if any(d < 1 or d > 7 for d in self.dias_semana):
            raise ValueError("dias_semana usa 1 (segunda) a 7 (domingo)")
        self.dias_semana = sorted(set(self.dias_semana))
        return self


class CircuitoNovo(CircuitoEntrada):
    no_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    canal: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")


_CAMPOS = ("nome", "sensor", "disjuntor_a", "fp_min", "standby_w", "janela_inicio",
           "janela_fim", "dias_semana", "max_continuo_min", "ativo")


@app.get("/api/circuitos")
def listar_circuitos(conn: Conn, incluir_inativos: bool = False):
    return conn.execute(
        "SELECT * FROM circuitos WHERE ativo OR %s ORDER BY nome", (incluir_inativos,)
    ).fetchall()


@app.post("/api/circuitos", status_code=201)
def criar_circuito(conn: Conn, dados: CircuitoNovo):
    cid = f"{dados.no_id}.{dados.canal}"
    with conn.transaction():
        conn.execute("INSERT INTO nos (id) VALUES (%s) ON CONFLICT DO NOTHING", (dados.no_id,))
        linha = conn.execute(
            f"INSERT INTO circuitos (id, no_id, canal, configurado, {', '.join(_CAMPOS)})"
            f" VALUES (%s, %s, %s, true, {', '.join(['%s'] * len(_CAMPOS))})"
            " ON CONFLICT (id) DO NOTHING RETURNING *",
            (cid, dados.no_id, dados.canal, *(getattr(dados, k) for k in _CAMPOS)),
        ).fetchone()
    if linha is None:
        raise HTTPException(409, f"o circuito {cid} já existe")
    return linha


@app.put("/api/circuitos/{cid}")
def atualizar_circuito(conn: Conn, cid: str, dados: CircuitoEntrada):
    linha = conn.execute(
        f"UPDATE circuitos SET configurado = true, {', '.join(f'{k} = %s' for k in _CAMPOS)}"
        " WHERE id = %s RETURNING *",
        (*(getattr(dados, k) for k in _CAMPOS), cid),
    ).fetchone()
    if linha is None:
        raise HTTPException(404, "circuito não encontrado")
    return linha


class NoEntrada(BaseModel):
    descricao: str = Field("", max_length=120)


@app.get("/api/nos")
def listar_nos(conn: Conn):
    return conn.execute(
        "SELECT n.*, (SELECT count(*) FROM circuitos c WHERE c.no_id = n.id AND c.ativo)"
        " AS circuitos FROM nos n ORDER BY n.id"
    ).fetchall()


@app.put("/api/nos/{no_id}")
def atualizar_no(conn: Conn, no_id: str, dados: NoEntrada):
    linha = conn.execute(
        "UPDATE nos SET descricao = %s WHERE id = %s RETURNING *", (dados.descricao, no_id)
    ).fetchone()
    if linha is None:
        raise HTTPException(404, "nó não encontrado")
    return linha


# ---- alertas ---------------------------------------------------------------------------

@app.get("/api/alertas")
def listar_alertas(
    conn: Conn, estado_: Annotated[Literal["abertos", "todos"], Query(alias="estado")] = "todos",
    limite: int = Query(100, ge=1, le=1000),
):
    return conn.execute(
        "SELECT a.*, c.nome AS circuito_nome FROM alertas a"
        " LEFT JOIN circuitos c ON c.id = a.circuito_id"
        " WHERE %s = 'todos' OR a.resolvido_em IS NULL"
        " ORDER BY a.resolvido_em IS NULL DESC, a.aberto_em DESC LIMIT %s",
        (estado_, limite),
    ).fetchall()


class Reconhecimento(BaseModel):
    por: str = Field("síndico", min_length=1, max_length=60)


@app.post("/api/alertas/{aid}/reconhecer")
def reconhecer_alerta(conn: Conn, aid: int, dados: Reconhecimento):
    linha = conn.execute(
        "UPDATE alertas SET reconhecido_em = now(), reconhecido_por = %s"
        " WHERE id = %s AND reconhecido_em IS NULL RETURNING *",
        (dados.por, aid),
    ).fetchone()
    if linha is None:
        raise HTTPException(404, "alerta não encontrado ou já reconhecido")
    return linha


# ---- configuração ----------------------------------------------------------------------

class ConfiguracaoEntrada(BaseModel):
    nome_local: str | None = Field(None, min_length=1, max_length=80)
    tarifa_kwh: float | None = Field(None, ge=0, le=20)
    pct_disjuntor: float | None = Field(None, gt=0, le=1)
    min_fora_janela: int | None = Field(None, ge=1, le=240)
    janela_fp_min: int | None = Field(None, ge=1, le=60)
    no_offline_s: int | None = Field(None, ge=30, le=86_400)


@app.get("/api/configuracao")
def ler_configuracao(conn: Conn):
    return {r["chave"]: r["valor"] for r in conn.execute("SELECT * FROM configuracao")}


@app.put("/api/configuracao")
def salvar_configuracao(conn: Conn, dados: ConfiguracaoEntrada):
    with conn.transaction():
        for chave, valor in dados.model_dump(exclude_none=True).items():
            conn.execute(
                "INSERT INTO configuracao (chave, valor) VALUES (%s, %s)"
                " ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor",
                (chave, Jsonb(valor)),
            )
    return ler_configuracao(conn)


# ---- análises --------------------------------------------------------------------------

@app.get("/api/analise/ranking")
def ranking(conn: Conn, inicio: datetime | None = None, fim: datetime | None = None):
    return analise.ranking(conn, *_periodo(inicio, fim, timedelta(days=30)))


@app.get("/api/analise/perfil")
def perfil(conn: Conn, dias: int = Query(28, ge=1, le=365), circuito: str | None = None):
    return analise.perfil(conn, fuso(), dias, circuito)


@app.get("/api/analise/comparacao")
def comparacao(conn: Conn, periodo: Literal["dia", "semana", "mes"] = "semana"):
    return analise.comparacao(conn, fuso(), periodo, datetime.now(UTC))


@app.get("/api/analise/acumulado")
def acumulado(conn: Conn):
    return analise.acumulado_mes(conn, fuso(), datetime.now(UTC))


@app.get("/api/analise/fora-de-janela")
def fora_de_janela(conn: Conn, inicio: datetime | None = None, fim: datetime | None = None):
    return analise.fora_de_janela(conn, fuso(), *_periodo(inicio, fim, timedelta(days=7)))


@app.get("/api/analise/projecao")
def projecao(conn: Conn):
    return analise.projecao(conn, fuso(), datetime.now(UTC))


@app.get("/api/exportar.csv")
def exportar(conn: Conn, inicio: datetime | None = None, fim: datetime | None = None):
    """Consumo por hora e circuito, para planilhas e para o relatório."""
    i, f = _periodo(inicio, fim, timedelta(days=30))
    linhas = conn.execute(
        "SELECT h.bucket, h.circuito_id, c.nome, h.energia_wh / 1000 AS kwh,"
        " h.potencia_max_w, h.corrente_max_a, h.soma_p / nullif(h.soma_s, 0) AS fp"
        " FROM leituras_1h h JOIN circuitos c ON c.id = h.circuito_id"
        " WHERE h.bucket >= %s AND h.bucket < %s ORDER BY h.bucket, c.nome",
        (i, f),
    ).fetchall()
    tz = fuso()
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(["hora", "circuito", "nome", "kwh", "potencia_max_w", "corrente_max_a", "fp"])
    for r in linhas:
        w.writerow([
            r["bucket"].astimezone(tz).strftime("%Y-%m-%d %H:%M"), r["circuito_id"], r["nome"],
            f"{r['kwh']:.4f}".replace(".", ","),
            f"{r['potencia_max_w']:.1f}".replace(".", ","),
            f"{r['corrente_max_a']:.2f}".replace(".", ","),
            f"{r['fp']:.3f}".replace(".", ",") if r["fp"] is not None else "",
        ])
    return StreamingResponse(
        iter([saida.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="energia-sense.csv"'},
    )


# O painel é estático e fala só com /api.
app.mount(
    "/", StaticFiles(directory=str(resources.files("energia") / "web"), html=True), name="web"
)
