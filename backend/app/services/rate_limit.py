"""Failed-login limiter. Uses Redis when available, otherwise an in-process store."""

import contextlib
import logging
import time
from threading import Lock

import redis

from app.core.config import get_settings

log = logging.getLogger(__name__)


class LoginLimiter:
    def __init__(self) -> None:
        self._memory: dict[str, list[float]] = {}
        self._lock = Lock()
        self._redis: redis.Redis | None = None
        url = get_settings().redis_url
        if url:
            try:
                client = redis.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5)
                client.ping()
                self._redis = client
            except redis.RedisError:
                log.warning("Redis unavailable; using in-memory login limiter")

    @staticmethod
    def _key(username: str, ip: str | None) -> str:
        return f"login-fail:{username.lower()}:{ip or '-'}"

    def is_blocked(self, username: str, ip: str | None) -> bool:
        s = get_settings()
        key = self._key(username, ip)
        if self._redis is not None:
            try:
                count = int(self._redis.get(key) or 0)
                return count >= s.login_max_attempts
            except redis.RedisError:
                pass
        with self._lock:
            now = time.monotonic()
            hits = [t for t in self._memory.get(key, []) if now - t < s.login_window_seconds]
            self._memory[key] = hits
            return len(hits) >= s.login_max_attempts

    def record_failure(self, username: str, ip: str | None) -> None:
        s = get_settings()
        key = self._key(username, ip)
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, s.login_window_seconds)
                pipe.execute()
                return
            except redis.RedisError:
                pass
        with self._lock:
            self._memory.setdefault(key, []).append(time.monotonic())

    def reset(self, username: str, ip: str | None) -> None:
        key = self._key(username, ip)
        if self._redis is not None:
            with contextlib.suppress(redis.RedisError):
                self._redis.delete(key)
        with self._lock:
            self._memory.pop(key, None)


_limiter: LoginLimiter | None = None


def get_limiter() -> LoginLimiter:
    global _limiter
    if _limiter is None:
        _limiter = LoginLimiter()
    return _limiter
