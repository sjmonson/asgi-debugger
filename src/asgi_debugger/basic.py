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
        
    async def send_wrapper(self, message: Message, send: Send, state: dict):
        self.logger.info(
            '[%s] [INFO] Got request data: %s',
            time.strftime("%Y-%m-%d %H:%M:%S %z"),
            message
        )
        if message["type"] == "http.request":
            data = QueryLoggerMiddleware._clean_data(message.get("body", b""))
            try:
                state["request_data"] = json.loads(data)
            except json.JSONDecodeError:
                state["request_data"] = data
        if message["type"] == "http.response.body":
            data = QueryLoggerMiddleware._clean_data(message.get("body", b""))
            try:
                response_data = json.loads(data)
            except json.JSONDecodeError:
                response_data = data

            if response_data and response_data != "[DONE]":
                state["response_data"] = response_data

        await send(message)

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Ignore non-HTTP scopes
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        state = {}

        try:
            await self.app(scope, receive, self.send_factory(send, state))

        finally:
            self.logger.info(
                '[%s] [INFO] "%s %s"\nrequest: %s\nresponse: %s',
                time.strftime("%Y-%m-%d %H:%M:%S %z"),
                scope["method"],
                scope["path"],
                state.get("request_data", "None"),
                state.get("response_data", "None"),
            )
