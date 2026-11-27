"""Accept gzip-compressed request bodies (phones compress GPS uploads ~4-5x to save data)."""

import zlib
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_COMPRESSED = 25 * 1024 * 1024
MAX_INFLATED = 60 * 1024 * 1024  # zip-bomb guard


class GzipRequestMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        if headers.get(b"content-encoding", b"").lower() != b"gzip":
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            body = message.get("body", b"")
            size += len(body)
            if size > MAX_COMPRESSED:
                await _reply(send, 413, b'{"detail":"Request too large"}')
                return
            chunks.append(body)
            more = message.get("more_body", False)
        try:
            d = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
            data = d.decompress(b"".join(chunks), MAX_INFLATED)
            if d.unconsumed_tail:
                await _reply(send, 413, b'{"detail":"Request too large"}')
                return
        except zlib.error:
            await _reply(send, 400, b'{"detail":"Body is not valid gzip"}')
            return

        new_headers: list[Any] = [
            (k, v) for k, v in scope["headers"] if k not in (b"content-encoding", b"content-length")
        ]
        new_headers.append((b"content-length", str(len(data)).encode()))
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": data, "more_body": False}

        await self.app({**scope, "headers": new_headers}, replay, send)


async def _reply(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
