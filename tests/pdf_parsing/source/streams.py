from collections.abc import AsyncIterator

import anyio
import httpx2


class ChunkStream(httpx2.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.consumed = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:
        await anyio.lowlevel.checkpoint()
        self.closed = True
