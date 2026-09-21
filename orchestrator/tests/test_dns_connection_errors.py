"""Deterministic reproduction of uvloop's abandoned resolver future (#746)."""
import asyncio
import gc
import logging
import socket

import pytest

from app.core.dns_connection_errors import install_dns_exception_handler


def test_cancelled_connection_retrieves_late_dns_failure(caplog):
    uvloop = pytest.importorskip("uvloop")
    loop = uvloop.new_event_loop()
    install_dns_exception_handler(loop)
    cancelled = []

    async def reproduce():
        async def create_connection():
            # Matches uvloop 0.22.1: _getaddrinfo Future + asyncio.wait.
            # asyncio.wait does not cancel that future when its caller times out.
            resolver = loop.create_future()
            loop.call_later(.02, resolver.set_exception,
                            socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"))
            try:
                await asyncio.wait([resolver])
            finally:
                cancelled.append(True)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(create_connection(), timeout=.001)
        assert cancelled == [True]
        await asyncio.sleep(.04)
        gc.collect()

    try:
        with caplog.at_level(logging.WARNING):
            loop.run_until_complete(reproduce())
        assert "Future exception was never retrieved" not in caplog.text
        warnings = [r for r in caplog.records if r.name == "app.core.dns_connection_errors"]
        assert len(warnings) == 1
        assert isinstance(warnings[0].exc_info[1], socket.gaierror)
    finally:
        loop.close()


def test_handler_is_idempotent_and_standard_loop_unchanged():
    uvloop = pytest.importorskip("uvloop")
    from unittest.mock import Mock
    for loop in (uvloop.new_event_loop(), asyncio.new_event_loop()):
        try:
            previous = Mock()
            loop.set_exception_handler(previous)
            install_dns_exception_handler(loop)
            handler = loop.get_exception_handler()
            install_dns_exception_handler(loop)
            assert loop.get_exception_handler() is handler
            if not isinstance(loop, uvloop.Loop):
                assert handler is previous
            context = {"message": "unrelated", "exception": RuntimeError("failure")}
            loop.call_exception_handler(context)
            previous.assert_called_once_with(loop, context)
        finally:
            loop.close()


@pytest.mark.parametrize("kind", ["runtime", "traceback", "task", "cancelled", "pending", "message"])
def test_other_errors_pass_through_unchanged(kind):
    uvloop = pytest.importorskip("uvloop")
    from unittest.mock import Mock
    loop = uvloop.new_event_loop()
    previous = Mock()
    loop.set_exception_handler(previous)
    install_dns_exception_handler(loop)
    future = loop.create_future()
    error = socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")
    message = "Future exception was never retrieved"
    try:
        if kind == "runtime":
            error = RuntimeError("unrelated")
        elif kind == "traceback":
            try:
                raise error
            except socket.gaierror as caught:
                error = caught
        elif kind == "task":
            async def failed():
                raise error
            future = loop.create_task(failed())
            loop.run_until_complete(asyncio.sleep(0))
            message = "Task exception was never retrieved"
        elif kind == "cancelled":
            future.cancel()
        elif kind == "message":
            message = "different future failure"
        if kind not in {"task", "pending", "cancelled"}:
            future.set_exception(error)
        context = {"message": message, "future": future, "exception": error}
        loop.call_exception_handler(context)
        previous.assert_called_once_with(loop, context)
    finally:
        if future.done() and not future.cancelled():
            future.exception()
        else:
            future.cancel()
        loop.close()


def test_unrelated_error_reaches_default_handler(caplog):
    uvloop = pytest.importorskip("uvloop")
    loop = uvloop.new_event_loop()
    try:
        install_dns_exception_handler(loop)
        loop.call_exception_handler({"message": "unrelated failure", "exception": ValueError("bad")})
        assert "unrelated failure" in caplog.text
        assert "ValueError: bad" in caplog.text
    finally:
        loop.close()


def test_matching_future_exception_is_actually_retrieved(caplog):
    uvloop = pytest.importorskip("uvloop")
    loop = uvloop.new_event_loop()

    class TrackedFuture(asyncio.Future):
        retrievals = 0

        def exception(self):
            self.retrievals += 1
            return super().exception()

    future = TrackedFuture(loop=loop)
    error = socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")
    future.set_exception(error)
    try:
        install_dns_exception_handler(loop)
        loop.call_exception_handler({
            "message": "Future exception was never retrieved",
            "exception": error,
            "future": future,
        })
        assert future.retrievals == 1
        assert "[uvloop resolver]" in caplog.text
    finally:
        future.exception()
        loop.close()
