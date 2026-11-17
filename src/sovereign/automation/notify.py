"""Notifications sortantes (webhook signé HMAC) : Slack/Teams via passerelle, n8n, Zapier, serveur maison…

Le corps est un JSON compatible avec les webhooks entrants Slack (champ ``text``) et contient aussi les données
structurées. Sans URL configurée, la notification est journalisée et ignorée (aucune erreur).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sovereign.config import AutomationSettings
from sovereign.core.security import sign_payload

log = logging.getLogger("sovereign.notify")


class Notifier:
    def __init__(self, settings: AutomationSettings, transport: Any = None) -> None:
        self.settings = settings
        self._transport = transport
        self.sent: list[dict[str, Any]] = []

    @property
    def configured(self) -> bool:
        return bool(self.settings.notify_webhook_url)

    def send(self, title: str, text: str, data: dict[str, Any] | None = None, level: str = "info") -> bool:
        body = {"text": f"*SOVEREIGN — {title}*\n{text}", "title": title, "level": level, "data": data or {}}
        self.sent.append(body)
        if not self.configured:
            log.info("notification (non envoyée, aucun webhook configuré) : %s", title)
            return False
        raw = json.dumps(body, ensure_ascii=False, default=str).encode()
        headers = {"Content-Type": "application/json"}
        if self.settings.notify_webhook_secret:
            headers.update(sign_payload(self.settings.notify_webhook_secret, raw))
        try:
            if self._transport is not None:
                self._transport(self.settings.notify_webhook_url, raw, headers)
            else:
                import httpx2

                response = httpx2.post(self.settings.notify_webhook_url or "", content=raw, headers=headers,
                                       timeout=10.0)
                response.raise_for_status()
        except Exception as exc:
            log.warning("échec d'envoi de notification : %s", exc)
            return False
        return True
