from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from energia.regras import (
    Amostra,
    Circuito,
    Parametros,
    avaliar_circuito,
    avaliar_no,
    dentro_janela,
    fp_agregado,
)

SP = ZoneInfo("America/Sao_Paulo")
P = Parametros()


def em(dia: int, hora: int, minuto: int = 0) -> datetime:
    # 21/09/2026 é segunda-feira
    return datetime(2026, 9, 20 + dia, hora, minuto, tzinfo=SP)


def serie(fim: datetime, minutos: int, w: float, i: float = 1.0, fp: float | None = 0.95,
          passo_s: int = 5) -> list[Amostra]:
    n = minutos * 60 // passo_s
    return [Amostra(fim - timedelta(seconds=passo_s * k), w, i, fp) for k in range(n, -1, -1)]


def tipos(conds):
    return {c.tipo for c in conds}


HALL = Circuito("n.c3", "Halls", "n", disjuntor_a=16, standby_w=60,
                janela_inicio=time(17, 30), janela_fim=time(7, 0))
BOMBA = Circuito("n.c1", "Bombas", "n", disjuntor_a=20, standby_w=50, max_continuo_min=40)


class TestJanela:
    def test_sem_janela_sempre_dentro(self):
        assert dentro_janela(em(1, 3), BOMBA)

    def test_janela_que_atravessa_meia_noite(self):
        assert dentro_janela(em(1, 18), HALL)
        assert dentro_janela(em(2, 3), HALL)
        assert not dentro_janela(em(2, 12), HALL)
        assert not dentro_janela(em(2, 7, 0), HALL)

    def test_madrugada_pertence_ao_dia_anterior(self):
        so_seg = Circuito("x", "x", "n", janela_inicio=time(22), janela_fim=time(2), dias_semana=(1,))
        assert dentro_janela(em(1, 23), so_seg)   # segunda 23h
        assert dentro_janela(em(2, 1), so_seg)    # madrugada de terça, janela de segunda
        assert not dentro_janela(em(2, 23), so_seg)

    def test_janela_diurna_por_dia_da_semana(self):
        salao = Circuito("x", "x", "n", janela_inicio=time(8), janela_fim=time(23),
                         dias_semana=(6, 7))
        assert dentro_janela(em(6, 10), salao)    # sábado
        assert not dentro_janela(em(3, 10), salao)


def test_fp_agregado_pondera_pela_potencia():
    amostras = [Amostra(em(1, 1), 1000, 5, 1.0), Amostra(em(1, 1), 100, 1, 0.5)]
    # P = 1100, S = 1000 + 200
    assert round(fp_agregado(amostras), 4) == round(1100 / 1200, 4)


def test_consumo_normal_nao_gera_alerta():
    agora = em(2, 20)
    assert avaliar_circuito(HALL, serie(agora, 15, 400, 3.4), agora, P) == []


def test_sobrecorrente_acima_de_80_por_cento_do_disjuntor():
    agora = em(2, 20)
    conds = avaliar_circuito(HALL, serie(agora, 2, 1700, 13.5), agora, P)
    assert tipos(conds) == {"sobrecorrente"}
    assert conds[0].limite == 12.8


def test_fp_baixo_com_carga():
    agora = em(2, 20)
    conds = avaliar_circuito(HALL, serie(agora, 6, 400, 5, fp=0.6), agora, P)
    assert tipos(conds) == {"fp_baixo"}


def test_fp_baixo_ignora_carga_em_standby():
    agora = em(2, 20)
    assert avaliar_circuito(HALL, serie(agora, 6, 20, 0.3, fp=0.3), agora, P) == []


def test_consumo_fora_de_janela():
    agora = em(2, 13)
    conds = avaliar_circuito(HALL, serie(agora, 12, 450, 3.8), agora, P)
    assert tipos(conds) == {"fora_de_janela"}


def test_fora_de_janela_exige_periodo_minimo_coberto():
    agora = em(2, 13)
    assert avaliar_circuito(HALL, serie(agora, 3, 450, 3.8), agora, P) == []


def test_fora_de_janela_nao_dispara_por_pico_isolado():
    agora = em(2, 13)
    amostras = serie(agora, 12, 25, 0.2)
    amostras[-1] = Amostra(agora, 600, 5, 0.9)
    assert avaliar_circuito(HALL, amostras, agora, P) == []


def test_bomba_ligada_sem_parar_indica_falha_de_boia():
    agora = em(2, 10)
    conds = avaliar_circuito(BOMBA, serie(agora, 45, 1900, 10), agora, P)
    assert tipos(conds) == {"operacao_continua"}


def test_bomba_em_ciclo_normal_nao_alerta():
    agora = em(2, 10)
    amostras = [a if (a.ts.minute % 35) < 12 else Amostra(a.ts, 0, 0, None)
                for a in serie(agora, 45, 1900, 10)]
    assert avaliar_circuito(BOMBA, amostras, agora, P) == []


def test_operacao_continua_precisa_do_periodo_inteiro():
    agora = em(2, 10)
    assert avaliar_circuito(BOMBA, serie(agora, 30, 1900, 10), agora, P) == []


def test_no_sem_dados():
    agora = em(2, 10)
    assert avaliar_no("n", agora - timedelta(minutes=1), agora, P) is None
    cond = avaliar_no("n", agora - timedelta(minutes=10), agora, P)
    assert cond.tipo == "sem_dados" and cond.no_id == "n"
    assert avaliar_no("n", None, agora, P) is None
