import time
from collections import defaultdict, deque
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class SimpleRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, enabled: bool, requests_per_minute: int) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.requests_per_minute = requests_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.enabled:
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        hits = self._hits[client]
        while hits and hits[0] <= now - 60:
            hits.popleft()

        if len(hits) >= self.requests_per_minute:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)

        hits.append(now)
        return await call_next(request)
