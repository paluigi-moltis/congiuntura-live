"""WebSocket hub — broadcast update-status changes to connected clients.

Messages are rendered as HTML fragments (htmx ws-extension convention:
content is swapped by element id, hx-swap-oob supported). Each connection
subscribes to one indicator kind via the query string (?kind=press|calendar)
so clients only receive fragments for elements present on their page.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

VALID_KINDS = {"press_releases", "calendar"}


def render_indicator_fragment(kind: str, status: dict[str, Any]) -> str:
    """Render the last-update indicator HTML for one pipeline.

    The outer div id matches the element on the page, so the htmx ws
    extension swaps it in place (OOB-by-id semantics).
    """
    label = "Press releases" if kind == "press_releases" else "Calendar"
    icon = "✓" if status.get("status") == "ok" else ("⚠" if status.get("status") == "partial" else "")
    icon_class = "ok" if status.get("status") == "ok" else ("warn" if status.get("status") == "partial" else "")
    time = status.get("last_run") or "never"
    details = status.get("details") or ""

    icon_html = f'<span class="lu-status {icon_class}">{icon}</span>' if icon else ""
    details_html = f'<span class="lu-details">{details}</span>' if details else ""

    return (
        f'<div class="last-update ws-live" id="last-update-{kind.replace("_", "-")}" '
        f'data-kind="{kind}">'
        f'<span class="lu-label">⏱ {label} — last update:</span> '
        f'<span class="lu-time">{time}</span> '
        f"{icon_html} {details_html}"
        f"</div>"
    )


class UpdateHub:
    """Fan-out broadcast for update-status HTML fragments."""

    def __init__(self) -> None:
        self._clients: dict[WebSocket, set[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, kinds: set[str]) -> None:
        await ws.accept()
        async with self._lock:
            self._clients[ws] = kinds
        logger.debug("WS client connected for %s (%d total)", kinds, len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(ws, None)
        logger.debug("WS client disconnected (%d total)", len(self._clients))

    async def broadcast(self, kind: str, fragment: str) -> None:
        """Send a fragment to clients subscribed to `kind`; drop dead sockets."""
        dead: list[WebSocket] = []
        async with self._lock:
            targets = [ws for ws, kinds in self._clients.items() if kind in kinds]
        for ws in targets:
            try:
                await ws.send_text(fragment)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)


# Module-level singleton shared by schedulers and the /ws endpoint
update_hub = UpdateHub()
