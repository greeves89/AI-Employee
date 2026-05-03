"""Tests for skill auto-injection path extraction and matching."""
from app.services.skill_auto_injector import extract_paths_from_text, match_paths


class TestExtractPaths:
    def test_single_absolute_path(self):
        paths = extract_paths_from_text("Fix /app/models/user.py")
        assert paths == ["/app/models/user.py"]

    def test_multiple_paths(self):
        paths = extract_paths_from_text("Fix /app/api/tasks.py and /app/models/skill.py")
        assert len(paths) == 2
        assert "/app/api/tasks.py" in paths
        assert "/app/models/skill.py" in paths

    def test_no_paths(self):
        assert extract_paths_from_text("Just regular text") == []

    def test_paths_in_backticks(self):
        paths = extract_paths_from_text("Look at `/app/services/foo.py`")
        assert "/app/services/foo.py" in paths

    def test_deduplication(self):
        paths = extract_paths_from_text("/app/foo.py and /app/foo.py again")
        assert len(paths) == 1

    def test_deep_nested_path(self):
        paths = extract_paths_from_text("/workspace/projects/ai-employee/orchestrator/alembic/versions/001.py")
        assert len(paths) == 1


class TestMatchPaths:
    def test_glob_star_star(self):
        assert match_paths(["/workspace/alembic/versions/001.py"], ["**/alembic/**"])

    def test_glob_star_extension(self):
        assert match_paths(["/app/models/user.py"], ["**/models/*.py"])

    def test_no_match(self):
        assert not match_paths(["/app/api/tasks.py"], ["**/alembic/**"])

    def test_empty_patterns(self):
        assert not match_paths(["/app/foo.py"], [])

    def test_empty_paths(self):
        assert not match_paths([], ["**/models/*.py"])

    def test_multiple_patterns_one_matches(self):
        assert match_paths(
            ["/app/api/tasks.py"],
            ["**/models/*.py", "**/api/*.py"],
        )
