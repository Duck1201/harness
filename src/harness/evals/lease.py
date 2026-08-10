import asyncio


class BenchmarkLeaseError(RuntimeError):
    pass


class BenchmarkLeaseCanceled(BenchmarkLeaseError):
    pass


class BenchmarkLease:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active_chats: set[str] = set()
        self._waiting: list[str] = []
        self._canceled: set[str] = set()
        self._owner: str | None = None

    @property
    def owner(self) -> str | None:
        return self._owner

    @property
    def waiting_benchmarks(self) -> tuple[str, ...]:
        return tuple(self._waiting)

    @property
    def active_chats(self) -> frozenset[str]:
        return frozenset(self._active_chats)

    async def enter_chat(self, chat_id: str) -> None:
        if not chat_id:
            raise ValueError("chat_id must not be empty")
        async with self._condition:
            if chat_id in self._active_chats:
                raise BenchmarkLeaseError(f"chat already active: {chat_id}")
            await self._condition.wait_for(
                lambda: self._owner is None and not self._waiting
            )
            self._active_chats.add(chat_id)

    async def leave_chat(self, chat_id: str) -> None:
        async with self._condition:
            if chat_id not in self._active_chats:
                raise BenchmarkLeaseError(f"chat is not active: {chat_id}")
            self._active_chats.remove(chat_id)
            self._condition.notify_all()

    async def acquire(self, run_id: str) -> None:
        if not run_id:
            raise ValueError("run_id must not be empty")
        async with self._condition:
            if self._owner == run_id:
                return
            if run_id in self._waiting:
                raise BenchmarkLeaseError(f"benchmark already waiting: {run_id}")
            self._waiting.append(run_id)
            self._condition.notify_all()
            try:
                await self._condition.wait_for(
                    lambda: run_id in self._canceled
                    or (
                        self._owner is None
                        and not self._active_chats
                        and self._waiting[0] == run_id
                    )
                )
            except asyncio.CancelledError:
                if run_id in self._waiting:
                    self._waiting.remove(run_id)
                self._condition.notify_all()
                raise
            if run_id in self._canceled:
                self._canceled.remove(run_id)
                raise BenchmarkLeaseCanceled(f"benchmark lease canceled: {run_id}")
            self._waiting.pop(0)
            self._owner = run_id
            self._condition.notify_all()

    async def release(self, run_id: str) -> None:
        async with self._condition:
            if self._owner != run_id:
                raise BenchmarkLeaseError(f"benchmark does not own lease: {run_id}")
            self._owner = None
            self._condition.notify_all()

    async def cancel(self, run_id: str) -> bool:
        async with self._condition:
            if self._owner == run_id:
                self._owner = None
                self._condition.notify_all()
                return True
            if run_id not in self._waiting:
                return False
            self._waiting.remove(run_id)
            self._canceled.add(run_id)
            self._condition.notify_all()
            return True
