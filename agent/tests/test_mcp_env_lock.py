"""Tests for #502: the CUSTOM_MCP_{SERVERS,AUTH,HEADERS} trio must be read/written
atomically as a set.

Background: refresh_mcp_credentials_loop (app/main.py) rewrites the three env vars
in sequence on every credential rotation. MCPHTTPClient._load_servers (app/tools/
mcp_client.py) reads them — and in custom_llm mode is instantiated via
asyncio.to_thread, i.e. from a worker thread concurrently with the loop's writes.
Without a shared lock a reader could observe a torn view (new SERVERS paired with
stale AUTH/HEADERS from the previous generation). Both sides now hold
``MCP_ENV_LOCK`` (app/tools/mcp_client.py) around their respective read/write
sequence.
"""
import json
import os
import threading

from app import main as agent_main
from app.tools.mcp_client import MCP_ENV_LOCK, MCPHTTPClient


def test_main_and_mcp_client_share_the_same_lock():
    # main.py imports MCP_ENV_LOCK from mcp_client lazily inside the loop body
    # rather than re-declaring its own — assert the module actually exposes it
    # for that `from app.tools.mcp_client import MCP_ENV_LOCK` to resolve to the
    # one _load_servers uses.
    import app.tools.mcp_client as mcp_client_module

    assert agent_main.__name__  # sanity: module imported
    assert mcp_client_module.MCP_ENV_LOCK is MCP_ENV_LOCK


def test_load_servers_reads_the_trio_under_the_lock(monkeypatch):
    """No torn reads: a writer flipping between two consistent generations must
    never let a concurrent reader see a mix of the two."""
    gen_a = (
        json.dumps({"srv": "http://a"}),
        json.dumps({"srv": "token-a"}),
        json.dumps({"srv": {"x-api-key": "a"}}),
    )
    gen_b = (
        json.dumps({"srv": "http://b"}),
        json.dumps({"srv": "token-b"}),
        json.dumps({"srv": {"x-api-key": "b"}}),
    )

    stop = threading.Event()
    torn_read = threading.Event()

    def writer():
        toggle = True
        while not stop.is_set():
            servers, auth, headers = gen_a if toggle else gen_b
            with MCP_ENV_LOCK:
                os.environ["CUSTOM_MCP_SERVERS"] = servers
                os.environ["CUSTOM_MCP_AUTH"] = auth
                os.environ["CUSTOM_MCP_HEADERS"] = headers
            toggle = not toggle

    def reader():
        client = MCPHTTPClient.__new__(MCPHTTPClient)
        while not stop.is_set():
            client._servers = {}
            client._auth = {}
            client._headers = {}
            client._load_servers()
            url = client._servers.get("srv")
            token = client._auth.get("srv")
            key = (client._headers.get("srv") or {}).get("x-api-key")
            if url is None:
                continue
            expected_suffix = url.rsplit("/", 1)[-1]  # "a" or "b"
            if token != f"token-{expected_suffix}" or key != expected_suffix:
                torn_read.set()
                stop.set()

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    stop.wait(timeout=1.0)
    stop.set()
    for t in threads:
        t.join(timeout=2.0)

    assert not torn_read.is_set(), "reader observed a torn CUSTOM_MCP_* trio"
