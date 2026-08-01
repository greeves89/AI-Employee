"""Guard tests for the security-critical properties of the clone-based skill
source fetcher added in #371 (phases 2+3, hardened in v1.119.1).

Three invariants must hold in ``SkillCrawlerService``:

1. ``_crawl_git_source`` rejects an admin-set clone URL / ref that begins with
   ``-`` BEFORE it is handed to ``git`` — otherwise git parses it as an option
   (argv flag smuggling, e.g. ``--upload-pack=…``).
2. The ``git clone`` argv uses a literal ``"--"`` separator so the URL and target
   directory can never be re-interpreted as flags.
3. Crawled skills pass through the install-time security gate
   (``check_skill_content``) in ``_sync_to_db``, exactly like API-imported skills
   (#192) — a crawled bundle must not bypass the postinstall-dropper gate.

Source-level AST guard so it runs without httpx/sqlalchemy/a live DB in the
agent container (full pytest with those deps is CI-only).
"""

import ast
import unittest
from pathlib import Path

_CRAWLER = (
    Path(__file__).resolve().parent.parent
    / "app" / "services" / "skill_crawler.py"
)


def _module() -> ast.Module:
    return ast.parse(_CRAWLER.read_text())


def _find_func(mod: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(mod):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in skill_crawler.py")


def _startswith_dash_args(func: ast.AST) -> set[str]:
    """Return the receiver names that are checked with ``.startswith("-")``."""
    guarded: set[str] = set()
    for n in ast.walk(func):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "startswith"
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and n.args[0].value == "-"
            and isinstance(n.func.value, ast.Name)
        ):
            guarded.add(n.func.value.id)
    return guarded


def _has_raise(func: ast.AST) -> bool:
    return any(isinstance(n, ast.Raise) for n in ast.walk(func))


def _attr_calls(func: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(func):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            names.add(n.func.attr)
    return names


def _name_calls(func: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(func):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            names.add(n.func.id)
    return names


class TestSkillGitSourceSecurity(unittest.TestCase):
    def test_clone_url_and_ref_dash_prefix_is_rejected(self):
        func = _find_func(_module(), "_crawl_git_source")
        guarded = _startswith_dash_args(func)
        # The URL (raw + credential-injected) and the ref must all be checked.
        self.assertIn(
            "url", guarded,
            "_crawl_git_source must reject a clone URL starting with '-'",
        )
        self.assertIn(
            "clone_url", guarded,
            "_crawl_git_source must reject a credential-injected URL starting with '-'",
        )
        self.assertIn(
            "ref", guarded,
            "_crawl_git_source must reject a ref starting with '-'",
        )
        self.assertTrue(
            _has_raise(func),
            "_crawl_git_source must raise when the dash-prefix guard trips",
        )

    def test_git_clone_uses_double_dash_separator(self):
        func = _find_func(_module(), "_crawl_git_source")
        literals = {
            n.value
            for n in ast.walk(func)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        self.assertIn(
            "--", literals,
            "git clone argv must include a literal '--' option terminator so the "
            "URL/target dir can never be parsed as flags",
        )

    def test_crawled_skills_pass_through_security_gate(self):
        func = _find_func(_module(), "_sync_to_db")
        called = _attr_calls(func) | _name_calls(func)
        self.assertIn(
            "check_skill_content", called,
            "_sync_to_db must run crawled skills through check_skill_content "
            "(the #192 install-time security gate) before importing them",
        )


if __name__ == "__main__":
    unittest.main()
