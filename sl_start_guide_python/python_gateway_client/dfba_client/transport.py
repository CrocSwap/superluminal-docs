from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dfba_client.types import DecodeError, UiSnapshot

if TYPE_CHECKING:
    from dfba_client.client import GatewayClient

PING_INTERVAL_SECONDS = 0.1


@dataclass(slots=True)
class WebSocketTransport:
    ws_url: str
    client: GatewayClient
    ping_interval_seconds: float = PING_INTERVAL_SECONDS
    reconnect_max_wait_seconds: float = 30.0
    snapshot_callback: Callable[[UiSnapshot], None] | None = None

    async def run_forever(self) -> None:
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:
            raise RuntimeError("websockets is required for WebSocketTransport") from exc

        previous_wait = 0.0
        next_wait = 1.0
        attempt = 0
        while True:
            attempt += 1
            closed = False
            self._log(f"websocket connecting attempt={attempt}")
            try:
                async with connect(self.ws_url, max_size=256 * 1024) as websocket:
                    previous_wait = 0.0
                    next_wait = 1.0
                    attempt = 0

                    async def send(payload: str) -> None:
                        await websocket.send(payload)

                    self.client.attach_sender(send)
                    await self.client.on_open()
                    await self._emit_snapshot()
                    ping_task = asyncio.create_task(self._ping_loop())
                    close_reason = ""
                    try:
                        async for raw in websocket:
                            if isinstance(raw, str):
                                try:
                                    await self.client.on_raw_message(raw)
                                except DecodeError as exc:
                                    detail = f"decode failed error={exc} payload={raw}"
                                    self._log(detail)
                                await self._emit_snapshot()
                            else:
                                self.client.record_error("binary_message_not_supported")
                                await self._emit_snapshot()
                    except Exception as exc:
                        close_reason = str(exc)
                        raise
                    finally:
                        ping_task.cancel()
                        ping_error: Exception | None = None
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:
                            ping_error = exc
                            if close_reason == "":
                                close_reason = str(exc)
                        self.client.detach_sender()
                        if not self.client.has_websocket_error():
                            close_detail = _websocket_close_detail(websocket, close_reason)
                            self._log(f"websocket disconnected {close_detail}".strip())
                            await self.client.on_close(close_detail)
                        await self._emit_snapshot()
                        closed = True
                        if ping_error is not None:
                            raise ping_error
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.client.has_websocket_error():
                    if not closed:
                        self.client.detach_sender()
                        await self._emit_snapshot()
                else:
                    self._log(f"websocket disconnected error={exc}")
                    if not closed:
                        self.client.detach_sender()
                        await self.client.on_close(str(exc))
                        await self._emit_snapshot()
            if not self.client.should_reconnect():
                self._log("websocket reconnect stopped reason=terminal_session")
                await self._emit_snapshot()
                return
            wait_seconds = min(next_wait, self.reconnect_max_wait_seconds)
            self._log(f"websocket reconnect retry_in_seconds={wait_seconds:g}")
            await self._emit_snapshot()
            await asyncio.sleep(wait_seconds)
            previous_wait, next_wait = next_wait, previous_wait + next_wait
            if next_wait <= 0:
                next_wait = 1.0

    async def _ping_loop(self) -> None:
        while True:
            await self.client.send_ping()
            await self._emit_snapshot()
            await asyncio.sleep(self.ping_interval_seconds)

    async def _emit_snapshot(self) -> None:
        if self.snapshot_callback is not None:
            self.snapshot_callback(self.client.snapshot())

    def _log(self, line: str) -> None:
        try:
            self.client.log_event(line)
        except RuntimeError:
            return


def _websocket_close_detail(websocket: object, fallback: str) -> str:
    close_code = getattr(websocket, "close_code", None)
    if isinstance(close_code, int):
        close_reason = getattr(websocket, "close_reason", "")
        reason = close_reason if isinstance(close_reason, str) else ""
        return f"code={close_code} reason={reason}"
    return fallback
