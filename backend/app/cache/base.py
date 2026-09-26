from typing import Any, Protocol


class CacheBackend(Protocol):
    name: str

    async def get(self, key: str) -> Any | None:
        ...

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ...

    async def delete(self, key: str) -> None:
        ...

    async def close(self) -> None:
        ...
