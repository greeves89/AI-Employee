"""Regression test for the skill-usage UnboundLocalError.

`agent_search_skills` re-imported `Task` inside a conditional block
(`if not resolved_task_id:`). Because a local import binds the name for the
whole function scope, the later `select(Task.id)` reference (reached when a
`task_id` is supplied and the conditional is skipped) raised
`UnboundLocalError: cannot access local variable 'Task'`.

The fix imports only `TaskStatus` locally and relies on the module-level
`Task` import. Guard against a regression by asserting `Task` is resolved as a
global, not a function-local, of the code object.
"""
from app.api import skill_marketplace


def test_task_is_not_function_local_in_agent_search_skills():
    code = skill_marketplace.agent_search_skills.__code__
    assert "Task" not in code.co_varnames, (
        "Task must resolve to the module-level import; a local `import Task` "
        "reintroduces the UnboundLocalError when task_id is supplied."
    )
    assert "Task" in skill_marketplace.__dict__, (
        "Task must be imported at module scope for the search path to use it."
    )
