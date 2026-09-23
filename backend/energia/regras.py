"""Regras de alerta, sem acesso a banco: recebem amostras e devolvem condições ativas.

O motor (motor.py) chama estas funções periodicamente e sincroniza o resultado com a
tabela de alertas: condição nova abre alerta, condição que sumiu resolve o alerta.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class Circuito:
    id: str
    nome: str
    no_id: str
    disjuntor_a: float | None = None
    fp_min: float = 0.92
    standby_w: float = 30
    janela_inicio: time | None = None
    janela_fim: time | None = None
    dias_semana: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)
    max_continuo_min: int | None = None


@dataclass(frozen=True)
class Amostra:
    ts: datetime  # já no fuso local
    potencia_w: float
    corrente_a: float
    fp: float | None = None


@dataclass(frozen=True)
class Parametros:
    pct_disjuntor: float = 0.8
    janela_fp_min: int = 5
    min_fora_janela: int = 10
    no_offline_s: int = 180
    min_amostras: int = 3
    # fração das amostras que precisa confirmar a condição
    fracao_min: float = 0.9


@dataclass(frozen=True)
class Condicao:
    tipo: str
    severidade: str
    mensagem: str
    circuito_id: str | None = None
    no_id: str | None = None
    valor: float | None = None
    limite: float | None = None

    @property
    def chave(self) -> tuple[str, str, str]:
        return (self.tipo, self.circuito_id or "", self.no_id or "")


def dentro_janela(momento: datetime, c: Circuito) -> bool:
    """Se o momento (hora local) está dentro da janela de operação do circuito."""
    if c.janela_inicio is None or c.janela_fim is None:
        return True
    t = momento.time()
    dia = momento.isoweekday()
    ini, fim = c.janela_inicio, c.janela_fim
    if ini < fim:
        return dia in c.dias_semana and ini <= t < fim
    if ini == fim:
        return dia in c.dias_semana
    # janela atravessa a meia-noite: a madrugada pertence à janela do dia anterior
    dia_anterior = 7 if dia == 1 else dia - 1
    return (t >= ini and dia in c.dias_semana) or (t < fim and dia_anterior in c.dias_semana)


def _recentes(amostras: list[Amostra], agora: datetime, minutos: float) -> list[Amostra]:
    corte = agora - timedelta(minutes=minutos)
    return [a for a in amostras if a.ts >= corte]


def _cobre(amostras: list[Amostra], agora: datetime, minutos: float) -> bool:
    """Se as amostras cobrem ao menos 80% da janela pedida (evita decidir com dados ralos)."""
    if not amostras:
        return False
    return (agora - amostras[0].ts) >= timedelta(minutes=minutos * 0.8)


def fp_agregado(amostras: list[Amostra]) -> float | None:
    """P total / S total. Média simples de fp distorce quando a carga varia."""
    soma_p = sum(a.potencia_w for a in amostras if a.fp)
    soma_s = sum(a.potencia_w / a.fp for a in amostras if a.fp)
    return soma_p / soma_s if soma_s else None


def avaliar_circuito(
    c: Circuito, amostras: list[Amostra], agora: datetime, p: Parametros
) -> list[Condicao]:
    amostras = sorted(amostras, key=lambda a: a.ts)
    ativas: list[Condicao] = []

    if c.disjuntor_a:
        ultimo_min = _recentes(amostras, agora, 1)
        limite = p.pct_disjuntor * c.disjuntor_a
        if ultimo_min:
            pico = max(a.corrente_a for a in ultimo_min)
            if pico >= limite:
                ativas.append(Condicao(
                    tipo="sobrecorrente", severidade="alta", circuito_id=c.id,
                    valor=round(pico, 2), limite=round(limite, 2),
                    mensagem=f"{c.nome}: corrente de {pico:.1f} A, acima de "
                             f"{p.pct_disjuntor:.0%} do disjuntor de {c.disjuntor_a:.0f} A.",
                ))

    carga = [a for a in _recentes(amostras, agora, p.janela_fp_min)
             if a.potencia_w > c.standby_w and a.fp]
    if len(carga) >= p.min_amostras:
        fp = fp_agregado(carga)
        if fp is not None and fp < c.fp_min:
            ativas.append(Condicao(
                tipo="fp_baixo", severidade="media", circuito_id=c.id,
                valor=round(fp, 3), limite=c.fp_min,
                mensagem=f"{c.nome}: fator de potência {fp:.3f}, abaixo do mínimo de "
                         f"{c.fp_min:.2f}.",
            ))

    janela = _recentes(amostras, agora, p.min_fora_janela)
    if (c.janela_inicio is not None and len(janela) >= p.min_amostras
            and _cobre(janela, agora, p.min_fora_janela)):
        fora = [a for a in janela if not dentro_janela(a.ts, c) and a.potencia_w > c.standby_w]
        if len(fora) / len(janela) >= p.fracao_min:
            media = sum(a.potencia_w for a in fora) / len(fora)
            ativas.append(Condicao(
                tipo="fora_de_janela", severidade="media", circuito_id=c.id,
                valor=round(media, 1), limite=c.standby_w,
                mensagem=f"{c.nome}: consumindo {media:.0f} W fora da janela de operação "
                         f"({c.janela_inicio:%H:%M}–{c.janela_fim:%H:%M}) há "
                         f"{p.min_fora_janela} min ou mais.",
            ))

    if c.max_continuo_min:
        periodo = _recentes(amostras, agora, c.max_continuo_min)
        # Aqui a cobertura precisa ser quase total: o alerta afirma "há mais de N min".
        cobre = periodo and (agora - periodo[0].ts) >= timedelta(minutes=c.max_continuo_min - 1)
        if len(periodo) >= p.min_amostras and cobre:
            ligadas = [a for a in periodo if a.potencia_w > c.standby_w]
            if len(ligadas) / len(periodo) >= p.fracao_min:
                ativas.append(Condicao(
                    tipo="operacao_continua", severidade="alta", circuito_id=c.id,
                    valor=round((agora - periodo[0].ts).total_seconds() / 60, 1),
                    limite=c.max_continuo_min,
                    mensagem=f"{c.nome}: ligado sem interrupção há mais de "
                             f"{c.max_continuo_min} min. Possível falha de boia ou comando.",
                ))

    return ativas


def avaliar_no(
    no_id: str, ultimo_contato: datetime | None, agora: datetime, p: Parametros
) -> Condicao | None:
    if ultimo_contato is None:
        return None
    silencio = (agora - ultimo_contato).total_seconds()
    if silencio < p.no_offline_s:
        return None
    return Condicao(
        tipo="sem_dados", severidade="alta", no_id=no_id,
        valor=round(silencio / 60, 1), limite=round(p.no_offline_s / 60, 1),
        mensagem=f"Nó {no_id} sem enviar leituras há {silencio / 60:.0f} min.",
    )
