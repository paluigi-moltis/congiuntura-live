"""Calendar module — NSO + ForexFactory release calendar collectors.

Ported from the standalone nso-calendar project, adapted to
congiuntura-live conventions: async httpx, TOML config, MongoDB storage.
"""

from .collectors import (
    BaseNSOCollector,
    EurostatCollector,
    IstatCollector,
    INECollector,
    DestatisCollector,
    INSEECollector,
    CSOCollector,
    ForexFactoryCollector,
)
from .repository import CalendarRepository
from .scheduler import CalendarPoller

__all__ = [
    "BaseNSOCollector",
    "EurostatCollector",
    "IstatCollector",
    "INECollector",
    "DestatisCollector",
    "INSEECollector",
    "CSOCollector",
    "ForexFactoryCollector",
    "CalendarRepository",
    "CalendarPoller",
]
