"""Rate limiting por API key, janela deslizante.

Implementação em memória, com uma limitação assumida: com N réplicas da API, o
limite efetivo vira N × limite, porque cada processo tem seu próprio contador.
A troca para Redis (`INCR` + `EXPIRE`, ou um script Lua de token bucket) mexe
só na classe `SlidingWindowLimiter` — a interface `hit()` e o middleware não
mudam. Está isolado justamente para isso.

Janela deslizante em vez de janela fixa porque janela fixa permite o dobro do
limite na virada: 60 requisições em 11:59:59 e mais 60 em 12:00:00.
"""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(slots=True)
class RateLimitState:
    allowed: bool
    remaining: int
    limit: int
    retry_after: int


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, identity: str) -> RateLimitState:
        now = time.monotonic()
        cutoff = now - self.window

        with self._lock:
            bucket = self._hits[identity]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= self.limit:
                retry_after = max(1, int(bucket[0] + self.window - now) + 1)
                return RateLimitState(False, 0, self.limit, retry_after)

            bucket.append(now)
            return RateLimitState(True, self.limit - len(bucket), self.limit, 0)

    def reset(self) -> None:
        """Usado pelos testes para isolar um caso do outro."""
        with self._lock:
            self._hits.clear()
