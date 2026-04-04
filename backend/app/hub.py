import asyncio
from collections import defaultdict


class OperationEventHub:
    """Fan-out for live operation events (multiple WebSocket subscribers per operation)."""

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def register(self, operation_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[operation_id].append(q)
        return q

    def unregister(self, operation_id: str, q: asyncio.Queue) -> None:
        lst = self._subs.get(operation_id)
        if not lst:
            return
        if q in lst:
            lst.remove(q)
        if not lst:
            self._subs.pop(operation_id, None)

    async def publish(self, operation_id: str, message: dict) -> None:
        for q in list(self._subs.get(operation_id, [])):
            await q.put(message)


hub = OperationEventHub()
