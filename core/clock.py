"""Meeting clocks. All timers (pauses, trigger interval, cooldown) use meeting time.

RealClock: meeting time = wall time since start.
SimClock: meeting time = (wall time since start) * speed, for replay.
"""
from __future__ import annotations

import asyncio
import time


class RealClock:
    speed = 1.0

    def __init__(self):
        self._t0 = time.monotonic()

    def now(self) -> float:
        return (time.monotonic() - self._t0) * self.speed

    async def sleep(self, meeting_seconds: float) -> None:
        await asyncio.sleep(max(0.0, meeting_seconds) / self.speed)

    async def sleep_until(self, t: float) -> None:
        await self.sleep(t - self.now())


class SimClock(RealClock):
    def __init__(self, speed: float = 1.0):
        super().__init__()
        self.speed = float(speed)
