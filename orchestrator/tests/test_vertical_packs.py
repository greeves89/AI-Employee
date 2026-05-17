"""Tests for vertical packs (industry starter kits, issue #159)."""

from app.core.vertical_packs import BUILTIN_VERTICAL_PACKS, get_pack
from app.core.agent_templates import BUILTIN_TEMPLATES


def test_packs_have_required_fields():
    assert len(BUILTIN_VERTICAL_PACKS) >= 3
    for pack in BUILTIN_VERTICAL_PACKS:
        for key in ("slug", "name", "description", "icon", "industry", "template_names"):
            assert key in pack, f"pack {pack.get('slug')} missing {key}"
        assert pack["template_names"], f"pack {pack['slug']} has no templates"


def test_pack_slugs_unique():
    slugs = [p["slug"] for p in BUILTIN_VERTICAL_PACKS]
    assert len(slugs) == len(set(slugs))


def test_get_pack():
    assert get_pack("dev-team") is not None
    assert get_pack("dev-team")["name"] == "Entwickler-Team"
    assert get_pack("does-not-exist") is None


def test_pack_templates_reference_real_builtin_templates():
    builtin_names = {t["name"] for t in BUILTIN_TEMPLATES}
    for pack in BUILTIN_VERTICAL_PACKS:
        for name in pack["template_names"]:
            assert name in builtin_names, (
                f"pack '{pack['slug']}' references unknown template '{name}'"
            )


def test_knowledge_entries_well_formed():
    for pack in BUILTIN_VERTICAL_PACKS:
        for entry in pack.get("knowledge_entries", []):
            assert entry.get("title")
            assert entry.get("content")
            assert isinstance(entry.get("tags", []), list)


def test_demo_tasks_well_formed():
    for pack in BUILTIN_VERTICAL_PACKS:
        demo = pack.get("demo_task")
        if demo is not None:
            assert demo.get("title")
            assert demo.get("prompt")
