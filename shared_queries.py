"""Coalesce simultaneous catalogue lookups within one room and config generation."""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic


@dataclass
class _Flight:
    task: asyncio.Task
    waiters: int = 0


class CatalogueRequests:
    def __init__(self):
        self.flights = {}
        self.identities = OrderedDict()

    async def join(self, key, search):
        flight = self.flights.get(key)
        if flight is None or flight.task.done():
            flight = _Flight(asyncio.create_task(search()))
            self.flights[key] = flight
        flight.waiters += 1
        try:
            return await asyncio.shield(flight.task)
        finally:
            flight.waiters -= 1
            if not flight.waiters:
                if self.flights.get(key) is flight:
                    self.flights.pop(key)
                if not flight.task.done():
                    flight.task.cancel()
                    await asyncio.gather(flight.task, return_exceptions=True)

    async def identity(self, title, resolve):
        # Only an exact catalogue media type and title are kept across requests.
        hit = self.identities.get(title)
        if hit and hit[0] > monotonic():
            self.identities.move_to_end(title)
            return hit[1]
        value = await resolve()
        if value:
            self.identities[title] = (monotonic() + 90, value)
            self.identities.move_to_end(title)
            if len(self.identities) > 128:
                self.identities.popitem(last=False)
        return value

    async def close(self):
        tasks = [flight.task for flight in self.flights.values()]
        self.flights.clear()
        self.identities.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
