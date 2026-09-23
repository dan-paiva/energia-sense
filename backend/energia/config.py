from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://energia:energia@localhost:5432/energia"

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_usuario: str | None = None
    mqtt_senha: str | None = None
    mqtt_prefixo: str = "energia"

    fuso: str = "America/Sao_Paulo"
    intervalo_regras_s: int = 30

    # Se PAINEL_SENHA estiver definida, o painel e a API pedem login (HTTP Basic).
    painel_usuario: str = "sindico"
    painel_senha: str | None = None

    # Notificação de alertas: webhook genérico e/ou bot do Telegram.
    webhook_url: str | None = None
    telegram_token: str | None = None
    telegram_chat_id: str | None = None


@lru_cache
def config() -> Config:
    return Config()
