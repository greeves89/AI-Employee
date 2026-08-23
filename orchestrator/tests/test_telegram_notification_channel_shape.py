"""Form test: every Telegram notification publish must be deliverable.

The only subscriber is ``TelegramBot._listen_notifications`` (app/telegram/bot.py).
It listens on the SINGULAR channel ``telegram:notification`` and sends
``data.get("text", "")``. A publisher that uses the plural channel, or omits
``text``, produces no error anywhere -- the send path is wrapped in a bare
``except``, so the caller sees success and the operator sees nothing.

That exact failure happened twice (#610, #637), which is why this is checked
against the source shape rather than at runtime: a missing delivery has no
runtime signal to assert on.
"""

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
SUBSCRIBED_CHANNEL = "telegram:notification"


def _publish_calls(tree: ast.AST):
    """Yield (call_node, channel) for every ``*.publish("telegram:notification*", ...)``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "publish"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if first.value.startswith("telegram:notification"):
                yield node, first.value


def _enclosing_scope(node: ast.AST) -> ast.AST:
    scope = getattr(node, "_parent", None)
    while scope is not None:
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return scope
        scope = getattr(scope, "_parent", None)
    raise AssertionError("node has no enclosing scope -- parent links not built")


def _payload_dicts(call: ast.Call) -> list[ast.Dict]:
    """The dict literal(s) that can end up as this publish call's payload.

    Handles both ``json.dumps({...})`` inline and ``json.dumps(payload)`` where
    ``payload`` is assigned in the same function. If a name resolves to several
    assignments, all of them must be well-formed, so all are returned.
    """
    assert len(call.args) >= 2, "publish() called without a payload"
    payload_arg = call.args[1]

    assert isinstance(payload_arg, ast.Call) and isinstance(payload_arg.func, ast.Attribute) \
        and payload_arg.func.attr == "dumps", \
        "payload is not a json.dumps(...) call -- this test cannot verify it"
    inner = payload_arg.args[0]

    if isinstance(inner, ast.Dict):
        return [inner]

    assert isinstance(inner, ast.Name), \
        f"payload argument is a {type(inner).__name__}; this test cannot verify it"

    scope = _enclosing_scope(call)
    found = [
        n.value
        for n in ast.walk(scope)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Dict)
        and any(isinstance(t, ast.Name) and t.id == inner.id for t in n.targets)
    ]
    assert found, f"could not resolve payload variable {inner.id!r} to a dict literal"
    return found


def _dict_keys(d: ast.Dict) -> set[str]:
    return {k.value for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _collect():
    cases = []
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child._parent = parent
        for call, channel in _publish_calls(tree):
            cases.append((path.relative_to(APP_DIR.parent), call.lineno, channel, call))
    return cases


CASES = _collect()


def test_at_least_one_publisher_is_scanned():
    """Guards the guard: an empty scan must not look like a pass."""
    assert len(CASES) >= 5, f"expected the known publishers to be found, got {len(CASES)}"


@pytest.mark.parametrize(
    "relpath,lineno,channel,call",
    CASES,
    ids=[f"{p}:{ln}" for p, ln, _c, _call in CASES],
)
def test_publisher_matches_subscriber_contract(relpath, lineno, channel, call):
    assert channel == SUBSCRIBED_CHANNEL, (
        f"{relpath}:{lineno} publishes to {channel!r}, but the only subscriber "
        f"(app/telegram/bot.py) listens on {SUBSCRIBED_CHANNEL!r}. The message is dropped."
    )
    for payload in _payload_dicts(call):
        keys = _dict_keys(payload)
        assert "text" in keys, (
            f"{relpath}:{lineno} publishes without a 'text' field (has: {sorted(keys)}). "
            f"The subscriber sends data.get('text', ''), so this arrives empty or is "
            f"rejected by Telegram."
        )
