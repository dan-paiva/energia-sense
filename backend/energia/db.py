import logging
from importlib import resources

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from energia.config import config

log = logging.getLogger(__name__)

# Chave arbitrária do advisory lock: impede que API e ingestão migrem ao mesmo tempo.
_TRAVA_MIGRACAO = 7_310_2026


def criar_pool(min_size: int = 1, max_size: int = 5) -> ConnectionPool:
    pool = ConnectionPool(
        config().database_url,
        min_size=min_size,
        max_size=max_size,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=False,
    )
    pool.open(wait=True, timeout=60)
    return pool


def migrar(url: str | None = None) -> list[str]:
    """Aplica, em ordem, os arquivos de energia/sql ainda não aplicados."""
    arquivos = sorted(
        (f for f in resources.files("energia.sql").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    aplicadas: list[str] = []
    with psycopg.connect(url or config().database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_TRAVA_MIGRACAO,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS migracoes ("
                " nome text PRIMARY KEY, aplicada_em timestamptz NOT NULL DEFAULT now())"
            )
            feitas = {r[0] for r in conn.execute("SELECT nome FROM migracoes")}
            for arq in arquivos:
                if arq.name in feitas:
                    continue
                log.info("aplicando migração %s", arq.name)
                with conn.transaction():
                    conn.execute(arq.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO migracoes (nome) VALUES (%s)", (arq.name,))
                aplicadas.append(arq.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_TRAVA_MIGRACAO,))
    return aplicadas


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    aplicadas = migrar()
    print("migrações aplicadas:", ", ".join(aplicadas) or "nenhuma (banco já atualizado)")
