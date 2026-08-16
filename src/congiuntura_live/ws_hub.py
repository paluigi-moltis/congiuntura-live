"""WebSocket hub — broadcast update-status changes to connected clients.

The schedulers call ``notify()`` after each run; every connected page
receives the new timestamps immediately and refreshes its indicator.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class UpdateHub:
    """Fan-out broadcast for update-status messages."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        logger.debug("WS client connected (%d total)", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        logger.debug("WS client disconnected (%d total)", len(self._clients))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to every connected client; drop dead sockets."""
        payload = json.dumps(message, default=_json_default)
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(value)


# Module-level singleton shared by schedulers and the /ws endpoint
update_hub = UpdateHub()
