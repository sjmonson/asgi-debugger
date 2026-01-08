from abc import ABC, abstractmethod
import time
import logging
import json
from typing import Awaitable, Callable

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "BasicMiddleware",
    "TimingMiddleware",
    "QueryLoggerMiddleware",
]


def map_state_to_headers(state: dict) -> dict:
    headers = {"X-Bug-" + k.replace("_", "-").title(): str(v) for k, v in state.items()}
    return headers


class BasicMiddleware(ABC):
    logger: logging.Logger
    app: ASGIApp

    def __init__(self, app: ASGIApp):
        self.logger = logging.getLogger("debug.access")
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(logging.StreamHandler())
        self.app = app

    @abstractmethod
    async def send_wrapper(self, message: Message, send: Send, state: dict): ...

    def send_factory(
        self, send: Send, state: dict
    ) -> Callable[[Message], Awaitable[None]]:
        return lambda message: self.send_wrapper(message, send, state)

    @abstractmethod
    async def __call__(self, scope: Scope, receive: Receive, send: Send): ...


class TimingMiddleware(BasicMiddleware):
    async def send_wrapper(self, message: Message, send: Send, state: dict):
        if message["type"] == "http.request":
            state["receive_time"] = time.monotonic()
        if message["type"] == "http.response.start":
            state["respond_time"] = time.monotonic()

            # Send debug headers at response start
            headers = MutableHeaders(raw=message["headers"])
            headers.update(map_state_to_headers(state))
            message["headers"] = headers.raw

        await send(message)

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Ignore non-HTTP scopes
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        state = {
            "start_time": time.monotonic(),
        }

        try:
            await self.app(scope, receive, self.send_factory(send, state))

        finally:
            state["end_time"] = time.monotonic()
            self.logger.info(
                '[%s] [INFO] "%s %s" with state: %s',
                time.strftime("%Y-%m-%d %H:%M:%S %z"),
                scope["method"],
                scope["path"],
                state,
            )


class QueryLoggerMiddleware(BasicMiddleware):
    @staticmethod
    def _clean_data(data: bytes) -> str:
        text = data.decode("utf-8").removeprefix("data: ").strip()
        return text

    def log_message(self, data: dict | str, type_: str | None, state: dict):
        self.logger.info("[QueryLogger] %s",
            json.dumps({
                "time": time.strftime("%Y-%m-%d %H:%M:%S %z"),
                "method": state.get("method"),
                "path": state.get("uri_path"),
                "type": type_,
                "data": data,
            })
        )
        
    async def send_wrapper(self, message: Message, send: Send, state: dict):
        data = QueryLoggerMiddleware._clean_data(message.get("body", b""))
        # Attempt to parse JSON data
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            pass

        self.log_message(data, message.get("type"), state)

        await send(message)

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Ignore non-HTTP scopes
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        state = {
            "method": scope["method"],
            "uri_path": scope["path"],
        }

        await self.app(scope, receive, self.send_factory(send, state))
