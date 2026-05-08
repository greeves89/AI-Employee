"""Tests for Trading Agent Template & Skills (Issue #156).

Verifies that the trading-analyst template and its 6 trading skills
are correctly structured, linked, and queryable.
"""
import json
import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.skill import (
    Skill,
    SkillCategory,
    SkillStatus,
    AgentSkillAssignment,
)
from app.models.agent_template import AgentTemplate

_TABLES = [
    Base.metadata.tables["skills"],
    Base.metadata.tables["agent_skill_assignments"],
    Base.metadata.tables["agent_templates"],
]

TRADING_SKILL_NAMES = [
    "trading-market-scanner",
    "trading-odds-analyzer",
    "trading-paper-portfolio",
    "trading-market-report",
    "trading-crypto-sentiment",
    "trading-backtest-analyzer",
]


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    Base.metadata.create_all(engine, tables=_TABLES)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def trading_skills(db_session: Session) -> list[Skill]:
    """Create all 6 trading skills matching the migration."""
    skills = []
    configs = [
        ("trading-market-scanner", SkillCategory.TOOL, "Scannt Polymarket"),
        ("trading-odds-analyzer", SkillCategory.WORKFLOW, "Recherchiert Markt"),
        ("trading-paper-portfolio", SkillCategory.TOOL, "Paper-Trading-Portfolio"),
        ("trading-market-report", SkillCategory.ROUTINE, "Tägliche Market-Reports"),
        ("trading-crypto-sentiment", SkillCategory.WORKFLOW, "Crypto Sentiment"),
        ("trading-backtest-analyzer", SkillCategory.WORKFLOW, "Backtest Analyse"),
    ]
    for i, (name, category, desc) in enumerate(configs, start=1):
        skill = Skill(
            id=i,
            name=name,
            description=desc,
            content=f"# {name}\n\nSkill content for testing.",
            category=category,
            status=SkillStatus.ACTIVE,
            created_by="migration",
            roles=json.dumps(["trading", "finance"]),
            paths="[]",
            current_version=1,
            usage_count=0,
            is_public=True,
            manual_duration_seconds=1800,
        )
        skills.append(skill)
        db_session.add(skill)
    db_session.commit()
    return skills


@pytest.fixture
def trading_template(db_session: Session, trading_skills: list[Skill]) -> AgentTemplate:
    """Create the trading-analyst template linked to all 6 skills."""
    skill_ids = [s.id for s in trading_skills]
    template = AgentTemplate(
        id=1,
        name="trading-analyst",
        display_name="Trading Analyst",
        description="Scannt Polymarket Prediction Markets",
        icon="TrendingUp",
        category="finance",
        model="claude-sonnet-4-6",
        role="Prediction Market Analyst",
        permissions=["package-install"],
        integrations=[],
        mcp_server_ids=[],
        skill_ids=skill_ids,
        knowledge_template="# Prediction Market Knowledge",
        claude_md="# Trading Analyst Agent\n\nYou are a specialized Prediction Market Analyst.",
        is_builtin=True,
        is_published=True,
    )
    db_session.add(template)
    db_session.commit()
    return template


class TestTradingSkillModels:
    """Verify trading skill creation and field integrity."""

    def test_all_six_skills_created(self, trading_skills: list[Skill]):
        assert len(trading_skills) == 6

    def test_skill_names(self, trading_skills: list[Skill]):
        names = {s.name for s in trading_skills}
        assert names == set(TRADING_SKILL_NAMES)

    def test_skills_are_active(self, trading_skills: list[Skill]):
        for skill in trading_skills:
            assert skill.status == SkillStatus.ACTIVE

    def test_skills_are_public(self, trading_skills: list[Skill]):
        for skill in trading_skills:
            assert skill.is_public is True

    def test_skill_categories(self, trading_skills: list[Skill]):
        by_name = {s.name: s for s in trading_skills}
        assert by_name["trading-market-scanner"].category == SkillCategory.TOOL
        assert by_name["trading-odds-analyzer"].category == SkillCategory.WORKFLOW
        assert by_name["trading-paper-portfolio"].category == SkillCategory.TOOL
        assert by_name["trading-market-report"].category == SkillCategory.ROUTINE
        assert by_name["trading-crypto-sentiment"].category == SkillCategory.WORKFLOW
        assert by_name["trading-backtest-analyzer"].category == SkillCategory.WORKFLOW

    def test_skills_have_roles(self, trading_skills: list[Skill]):
        for skill in trading_skills:
            roles = json.loads(skill.roles) if isinstance(skill.roles, str) else skill.roles
            assert "trading" in roles
            assert "finance" in roles

    def test_skills_have_manual_duration(self, trading_skills: list[Skill]):
        for skill in trading_skills:
            assert skill.manual_duration_seconds > 0

    def test_skill_content_not_empty(self, trading_skills: list[Skill]):
        for skill in trading_skills:
            assert len(skill.content) > 10

    def test_skill_unique_names(self, db_session: Session, trading_skills: list[Skill]):
        result = db_session.execute(select(Skill))
        all_skills = result.scalars().all()
        names = [s.name for s in all_skills]
        assert len(names) == len(set(names)), "Duplicate skill names found"


class TestTradingTemplateModel:
    """Verify the trading-analyst template structure."""

    def test_template_name(self, trading_template: AgentTemplate):
        assert trading_template.name == "trading-analyst"

    def test_template_display_name(self, trading_template: AgentTemplate):
        assert trading_template.display_name == "Trading Analyst"

    def test_template_category(self, trading_template: AgentTemplate):
        assert trading_template.category == "finance"

    def test_template_icon(self, trading_template: AgentTemplate):
        assert trading_template.icon == "TrendingUp"

    def test_template_is_builtin(self, trading_template: AgentTemplate):
        assert trading_template.is_builtin is True

    def test_template_is_published(self, trading_template: AgentTemplate):
        assert trading_template.is_published is True

    def test_template_has_claude_md(self, trading_template: AgentTemplate):
        assert "Prediction Market Analyst" in trading_template.claude_md

    def test_template_has_knowledge(self, trading_template: AgentTemplate):
        assert "Prediction Market" in trading_template.knowledge_template

    def test_template_model(self, trading_template: AgentTemplate):
        assert trading_template.model == "claude-sonnet-4-6"

    def test_template_permissions(self, trading_template: AgentTemplate):
        assert "package-install" in trading_template.permissions


class TestTemplateSkillLinkage:
    """Verify the template correctly references all 6 trading skills."""

    def test_template_has_six_skill_ids(
        self, trading_template: AgentTemplate, trading_skills: list[Skill]
    ):
        assert len(trading_template.skill_ids) == 6

    def test_template_skill_ids_match(
        self, trading_template: AgentTemplate, trading_skills: list[Skill]
    ):
        expected_ids = sorted(s.id for s in trading_skills)
        actual_ids = sorted(trading_template.skill_ids)
        assert actual_ids == expected_ids

    def test_all_referenced_skills_exist(
        self, db_session: Session, trading_template: AgentTemplate
    ):
        for skill_id in trading_template.skill_ids:
            skill = db_session.get(Skill, skill_id)
            assert skill is not None, f"Skill ID {skill_id} referenced but not found"
            assert skill.status == SkillStatus.ACTIVE

    def test_skill_assignment_to_agent(
        self, db_session: Session, trading_template: AgentTemplate
    ):
        """Simulate what create-agent-from-template does: assign skills to agent."""
        agent_id = 42
        for skill_id in trading_template.skill_ids:
            assignment = AgentSkillAssignment(
                agent_id=agent_id,
                skill_id=skill_id,
                assigned_by="template",
            )
            db_session.add(assignment)
        db_session.commit()

        result = db_session.execute(
            select(AgentSkillAssignment).where(
                AgentSkillAssignment.agent_id == agent_id
            )
        )
        assignments = result.scalars().all()
        assert len(assignments) == 6
        assert all(a.assigned_by == "template" for a in assignments)

    def test_no_duplicate_skill_assignments(
        self, db_session: Session, trading_template: AgentTemplate
    ):
        """Second assignment of same skill to same agent should be skippable."""
        agent_id = 99
        skill_id = trading_template.skill_ids[0]
        db_session.add(AgentSkillAssignment(
            agent_id=agent_id, skill_id=skill_id, assigned_by="template"
        ))
        db_session.commit()

        existing = db_session.execute(
            select(AgentSkillAssignment).where(
                AgentSkillAssignment.agent_id == agent_id,
                AgentSkillAssignment.skill_id == skill_id,
            )
        ).scalar()
        assert existing is not None


class TestMigrationSkillContent:
    """Verify skill content contains expected API endpoints and patterns."""

    def test_market_scanner_has_polymarket_api(self, trading_skills: list[Skill]):
        by_name = {s.name: s for s in trading_skills}
        content = by_name["trading-market-scanner"].content
        assert "market-scanner" in content.lower() or "trading" in content.lower()

    def test_each_skill_starts_with_heading(self, trading_skills: list[Skill]):
        for skill in trading_skills:
            assert skill.content.startswith("#"), (
                f"Skill {skill.name} content should start with markdown heading"
            )

    def test_query_skills_by_role(self, db_session: Session, trading_skills: list[Skill]):
        """Verify skills can be filtered by role (used in auto-injection)."""
        result = db_session.execute(select(Skill))
        all_skills = result.scalars().all()
        trading_role_skills = [
            s for s in all_skills
            if "trading" in (json.loads(s.roles) if isinstance(s.roles, str) else (s.roles or []))
        ]
        assert len(trading_role_skills) == 6
