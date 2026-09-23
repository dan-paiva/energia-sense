import logging

import httpx

from energia.config import Config

log = logging.getLogger(__name__)

_ROTULO = {"aberto": "ALERTA", "resolvido": "Resolvido"}


class Notificador:
    """Envia abertura e resolução de alertas. Sem destino configurado, só registra em log."""

    def __init__(self, cfg: Config, cliente: httpx.Client | None = None):
        self.cfg = cfg
        self.cliente = cliente or httpx.Client(timeout=10)

    def enviar(self, evento: str, alerta: dict) -> None:
        texto = f"[EnergIA Sense] {_ROTULO.get(evento, evento)}: {alerta['mensagem']}"
        log.info(texto)
        if self.cfg.webhook_url:
            self._postar(self.cfg.webhook_url, {"evento": evento, "alerta": alerta})
        if self.cfg.telegram_token and self.cfg.telegram_chat_id:
            self._postar(
                f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage",
                {"chat_id": self.cfg.telegram_chat_id, "text": texto},
            )

    def _postar(self, url: str, corpo: dict) -> None:
        # Falha de notificação não pode derrubar o motor de alertas.
        try:
            self.cliente.post(url, json=corpo).raise_for_status()
        except httpx.HTTPError as e:
            log.warning("falha ao notificar %s: %s", url.split("/bot")[0], e)
