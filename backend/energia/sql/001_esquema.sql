-- Esquema base do EnergIA Sense.
-- Leituras brutas em hypertable; agregações contínuas por minuto, hora e dia.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE nos (
    id              text PRIMARY KEY,
    descricao       text NOT NULL DEFAULT '',
    simulado        boolean NOT NULL DEFAULT false,
    status          text NOT NULL DEFAULT 'desconhecido',
    firmware        text,
    ultimo_contato  timestamptz,
    criado_em       timestamptz NOT NULL DEFAULT now()
);

-- O id do circuito é "<nó>.<canal>", para que dois nós nunca colidam.
-- Janela de operação: fora dela, consumo acima do standby vira alerta.
-- janela_inicio > janela_fim significa janela que atravessa a meia-noite.
-- dias_semana segue ISO 8601 (1 = segunda ... 7 = domingo).
CREATE TABLE circuitos (
    id                text PRIMARY KEY,
    no_id             text NOT NULL REFERENCES nos (id),
    canal             text NOT NULL,
    nome              text NOT NULL,
    sensor            text NOT NULL DEFAULT 'pzem' CHECK (sensor IN ('pzem', 'sct')),
    disjuntor_a       real CHECK (disjuntor_a > 0),
    fp_min            real NOT NULL DEFAULT 0.92 CHECK (fp_min BETWEEN 0 AND 1),
    standby_w         real NOT NULL DEFAULT 30 CHECK (standby_w >= 0),
    janela_inicio     time,
    janela_fim        time,
    dias_semana       smallint[] NOT NULL DEFAULT '{1,2,3,4,5,6,7}',
    max_continuo_min  integer CHECK (max_continuo_min > 0),
    ativo             boolean NOT NULL DEFAULT true,
    configurado       boolean NOT NULL DEFAULT false,
    criado_em         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (no_id, canal),
    CHECK ((janela_inicio IS NULL) = (janela_fim IS NULL))
);

CREATE TABLE leituras (
    ts                 timestamptz NOT NULL,
    circuito_id        text NOT NULL REFERENCES circuitos (id),
    seq                bigint,
    tensao_v           real,
    corrente_a         real NOT NULL,
    potencia_w         real NOT NULL,
    fp                 real,
    frequencia_hz      real,
    energia_total_kwh  double precision,
    -- energia consumida desde a leitura anterior do mesmo circuito
    energia_wh         double precision NOT NULL,
    PRIMARY KEY (circuito_id, ts)
);

SELECT create_hypertable('leituras', by_range('ts', INTERVAL '1 day'));

ALTER TABLE leituras SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'circuito_id',
    timescaledb.compress_orderby = 'ts'
);
SELECT add_compression_policy('leituras', INTERVAL '14 days');

CREATE TABLE alertas (
    id               bigserial PRIMARY KEY,
    tipo             text NOT NULL,
    severidade       text NOT NULL CHECK (severidade IN ('baixa', 'media', 'alta')),
    circuito_id      text REFERENCES circuitos (id),
    no_id            text REFERENCES nos (id),
    mensagem         text NOT NULL,
    valor            double precision,
    limite           double precision,
    aberto_em        timestamptz NOT NULL DEFAULT now(),
    atualizado_em    timestamptz NOT NULL DEFAULT now(),
    resolvido_em     timestamptz,
    reconhecido_em   timestamptz,
    reconhecido_por  text
);

-- No máximo um alerta aberto por (alvo, tipo).
CREATE UNIQUE INDEX alertas_um_aberto
    ON alertas (tipo, coalesce(circuito_id, ''), coalesce(no_id, ''))
    WHERE resolvido_em IS NULL;
CREATE INDEX alertas_aberto_em ON alertas (aberto_em DESC);

CREATE TABLE configuracao (
    chave  text PRIMARY KEY,
    valor  jsonb NOT NULL
);

INSERT INTO configuracao (chave, valor) VALUES
    ('nome_local', '"Condomínio A"'),
    ('tarifa_kwh', '0.95'),
    ('pct_disjuntor', '0.8'),
    ('min_fora_janela', '10'),
    ('janela_fp_min', '5'),
    ('no_offline_s', '180');

-- Mesma regra de regras.dentro_janela, para as análises em SQL.
CREATE FUNCTION dentro_janela(
    ts timestamptz, inicio time, fim time, dias smallint[], fuso text
) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN inicio IS NULL THEN true
        WHEN inicio < fim THEN
            extract(isodow FROM ts AT TIME ZONE fuso)::smallint = ANY (dias)
            AND (ts AT TIME ZONE fuso)::time >= inicio
            AND (ts AT TIME ZONE fuso)::time < fim
        WHEN inicio = fim THEN
            extract(isodow FROM ts AT TIME ZONE fuso)::smallint = ANY (dias)
        ELSE
            ((ts AT TIME ZONE fuso)::time >= inicio
             AND extract(isodow FROM ts AT TIME ZONE fuso)::smallint = ANY (dias))
            OR ((ts AT TIME ZONE fuso)::time < fim
             AND extract(isodow FROM (ts AT TIME ZONE fuso) - INTERVAL '1 day')::smallint = ANY (dias))
    END
$$;

-- Fator de potência agregado = P total / S total, com S = P / fp.
CREATE MATERIALIZED VIEW leituras_1min
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 minute', ts) AS bucket,
    circuito_id,
    sum(energia_wh)                                        AS energia_wh,
    avg(potencia_w)                                        AS potencia_media_w,
    max(potencia_w)                                        AS potencia_max_w,
    max(corrente_a)                                        AS corrente_max_a,
    avg(tensao_v)                                          AS tensao_media_v,
    sum(potencia_w) FILTER (WHERE fp > 0)                  AS soma_p,
    sum(potencia_w / fp) FILTER (WHERE fp > 0)             AS soma_s,
    count(*)                                               AS amostras
FROM leituras
GROUP BY 1, 2
WITH NO DATA;

CREATE MATERIALIZED VIEW leituras_1h
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 hour', bucket) AS bucket,
    circuito_id,
    sum(energia_wh)        AS energia_wh,
    max(potencia_max_w)    AS potencia_max_w,
    max(corrente_max_a)    AS corrente_max_a,
    sum(soma_p)            AS soma_p,
    sum(soma_s)            AS soma_s,
    sum(amostras)          AS amostras
FROM leituras_1min
GROUP BY 1, 2
WITH NO DATA;

CREATE MATERIALIZED VIEW leituras_1d
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 day', bucket, 'America/Sao_Paulo') AS bucket,
    circuito_id,
    sum(energia_wh)        AS energia_wh,
    max(potencia_max_w)    AS potencia_max_w,
    max(corrente_max_a)    AS corrente_max_a,
    sum(soma_p)            AS soma_p,
    sum(soma_s)            AS soma_s,
    sum(amostras)          AS amostras
FROM leituras_1h
GROUP BY 1, 2
WITH NO DATA;

-- start_offset de 7 dias cobre o reenvio de leituras que ficaram no buffer
-- do nó durante uma queda de rede.
SELECT add_continuous_aggregate_policy('leituras_1min',
    start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute');
SELECT add_continuous_aggregate_policy('leituras_1h',
    start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '10 minutes');
SELECT add_continuous_aggregate_policy('leituras_1d',
    start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 hour');
