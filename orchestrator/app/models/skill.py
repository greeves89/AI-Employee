"""Skill Marketplace — persistent, shareable, ratable skills.

A Skill is a reusable set of instructions (routine, template, workflow, pattern)
that can be assigned to agents. Skills can be created by users, agents, or
imported from external sources (GitHub repos, MCP registries).

Lifecycle: draft → active → (usage → rating → improvement)
Agent-proposed skills start as drafts and require user review.
"""

import enum

from sqlalchemy import Boolean, Enum, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SkillStatus(str, enum.Enum):
    DRAFT = "draft"          # Agent-proposed or freshly imported, needs review
    ACTIVE = "active"        # Approved and available in marketplace
    ARCHIVED = "archived"    # Deprecated, no longer assignable


class SkillCategory(str, enum.Enum):
    ROUTINE = "routine"       # Repeatable process ("how to deploy")
    TEMPLATE = "template"     # Document template ("meeting notes format")
    WORKFLOW = "workflow"      # Multi-step workflow ("PR review process")
    PATTERN = "pattern"       # Code/architecture pattern ("error handling")
    RECIPE = "recipe"         # Step-by-step guide ("set up monitoring")
    TOOL = "tool"             # Tool-specific skill ("use grep effectively")


class Skill(Base, TimestampMixin):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")  # SKILL.md body (markdown instructions)
    category: Mapped[SkillCategory] = mapped_column(Enum(SkillCategory), default=SkillCategory.ROUTINE)
    status: Mapped[SkillStatus] = mapped_column(Enum(SkillStatus), default=SkillStatus.ACTIVE)

    # Origin tracking
    created_by: Mapped[str] = mapped_column(String, default="user")  # "user", "agent:<agent_id>", "import:github"
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)  # GitHub repo URL if imported
    source_repo: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. "vercel-labs/skills"
    source_task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # task that produced this skill

    # Auto-activation (optional glob patterns — skill activates when task touches matching files)
    paths: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["**/alembic/**", "**/models/*.py"]
    # Role-based auto-assign (skill auto-assigned to agents with matching roles)
    roles: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["devops", "fullstack"]

    # Usage & quality
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)  # visible in marketplace


class AgentSkillAssignment(Base, TimestampMixin):
    """Junction table: which agents have which skills installed."""
    __tablename__ = "agent_skill_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    skill_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    assigned_by: Mapped[str] = mapped_column(String, default="user")  # "user", "auto:role", "auto:path", "agent"
