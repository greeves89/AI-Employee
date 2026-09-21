"""Attribute abandoned uvloop resolver failures without changing timeouts (#746).

uvloop 0.22.1 create_connection waits on internal DNS futures with asyncio.wait.
Cancelling that call leaves those futures alive; retrieving the *outer* task's
exception cannot retrieve their late exceptions. The loop exception hook is the
public API that exposes the abandoned future. This is an observability workaround,
not a resolver fix: retain a WARNING with the original exception for infra counts.
"""
import asyncio
import logging
import socket

logger = logging.getLogger(__name__)


class _DNSExceptionHandler:
    def __init__(self, previous):
        self.previous = previous

    def __call__(self, loop, context):
        future = context.get("future")
        error = context.get("exception")
        if (
            context.get("message") == "Future exception was never retrieved"
            and isinstance(future, asyncio.Future)
            and not isinstance(future, asyncio.Task)
            and future.done()
            and not future.cancelled()
            and isinstance(error, socket.gaierror)
            and error.__traceback__ is None
            and future.exception() is error
        ):
            # No host/caller is attached by uvloop. Do not invent attribution or
            # dump the context (which may contain application data).
            logger.warning(
                "[uvloop resolver] Late DNS failure after connection cancellation "
                "(matching uvloop resolver signature); connection timeout unchanged",
                exc_info=(type(error), error, None),
            )
            return
        if self.previous is not None:
            self.previous(loop, context)
        else:
            loop.default_exception_handler(context)


def install_dns_exception_handler(loop: asyncio.AbstractEventLoop) -> None:
    """Install once for the uvloop lifetime; preserve every other error handler."""
    try:
        import uvloop
    except ImportError:
        return
    if not isinstance(loop, uvloop.Loop):
        return
    previous = loop.get_exception_handler()
    if not isinstance(previous, _DNSExceptionHandler):
        loop.set_exception_handler(_DNSExceptionHandler(previous))
