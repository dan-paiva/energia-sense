"""Contrato das mensagens publicadas pelos nós sensores.

Tópico:  <prefixo>/<nó>/leituras
Exemplo:
    {"no": "no-01", "ts": 1758650000000, "seq": 42, "intervalo_s": 5,
     "fw": "0.1.0",
     "circuitos": [{"id": "c1", "v": 127.2, "i": 3.41, "p": 380.5,
                    "fp": 0.88, "f": 60.0, "e_kwh": 12.345}]}

ts é epoch em milissegundos; 0 significa que o nó ainda não sincronizou o relógio,
e a ingestão usa então a hora de recebimento.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field, ValidationError

ID = r"^[a-z0-9][a-z0-9_-]{0,31}$"

# Leituras datadas fora desta faixa em relação ao recebimento indicam relógio errado.
_PASSADO_MAX = timedelta(days=7)
_FUTURO_MAX = timedelta(minutes=5)


class LeituraCircuito(BaseModel):
    id: str = Field(pattern=ID)
    v: float | None = Field(None, ge=0, le=300)
    i: float = Field(ge=0, le=200)
    p: float = Field(ge=0, le=60_000)
    fp: float | None = Field(None, ge=0, le=1)
    f: float | None = Field(None, ge=40, le=70)
    e_kwh: float | None = Field(None, ge=0)


class Mensagem(BaseModel):
    no: str = Field(pattern=ID)
    ts: int = Field(0, ge=0)
    seq: int = Field(0, ge=0)
    intervalo_s: float = Field(5, gt=0, le=3600)
    fw: str | None = Field(None, max_length=32)
    circuitos: list[LeituraCircuito] = Field(min_length=1, max_length=16)


class PayloadInvalido(ValueError):
    pass


def interpretar(bruto: bytes) -> Mensagem:
    try:
        return Mensagem.model_validate_json(bruto)
    except ValidationError as e:
        raise PayloadInvalido(str(e)) from e


def resolver_ts(ts_ms: int, recebido_em: datetime) -> datetime:
    if ts_ms <= 0:
        return recebido_em
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    if recebido_em - _PASSADO_MAX <= ts <= recebido_em + _FUTURO_MAX:
        return ts
    return recebido_em


@dataclass(frozen=True)
class Contador:
    ts: datetime
    e_kwh: float | None


def energia_intervalo_wh(
    anterior: Contador | None,
    ts: datetime,
    e_kwh: float | None,
    potencia_w: float,
    intervalo_s: float,
) -> float:
    """Energia consumida desde a leitura anterior do circuito.

    Usa a diferença do contador acumulado do PZEM quando ela é confiável; caso
    contrário (primeira leitura, contador zerado, leitura fora de ordem vinda do
    buffer, salto implausível ou sensor sem contador), integra a potência no
    intervalo nominal.
    """
    integrada = potencia_w * intervalo_s / 3600
    if anterior is None or e_kwh is None or anterior.e_kwh is None or ts <= anterior.ts:
        return integrada
    delta_wh = (e_kwh - anterior.e_kwh) * 1000
    if delta_wh < 0:
        return integrada
    dt_s = (ts - anterior.ts).total_seconds()
    # O contador tem resolução de 1 Wh; a folga de 50 Wh absorve o arredondamento.
    if delta_wh > 1.5 * potencia_w * dt_s / 3600 + 50:
        return integrada
    return delta_wh
