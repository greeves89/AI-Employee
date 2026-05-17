"""Tests for task-step persistence (time-travel replay, issue #54)."""

import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.task import Task, TaskStatus
from app.models.task_step import TaskStep


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Base.metadata.tables["tasks"],
            Base.metadata.tables["task_steps"],
        ],
    )
    with Session(engine) as session:
        yield session


def _make_task(db) -> Task:
    task = Task(id="task-abc", title="t", prompt="p", status=TaskStatus.COMPLETED)
    db.add(task)
    db.commit()
    return task


def test_steps_persist_and_order(db_session):
    _make_task(db_session)
    events = ["system", "text", "tool_call", "tool_result", "result"]
    for i, et in enumerate(events):
        db_session.add(TaskStep(
            task_id="task-abc",
            sequence=i,
            event_type=et,
            event_data={"i": i},
            timestamp=datetime.now(timezone.utc),
        ))
    db_session.commit()

    steps = (
        db_session.query(TaskStep)
        .filter(TaskStep.task_id == "task-abc")
        .order_by(TaskStep.sequence.asc())
        .all()
    )
    assert [s.event_type for s in steps] == events
    assert [s.sequence for s in steps] == [0, 1, 2, 3, 4]
    assert steps[2].event_data == {"i": 2}


def test_duplicate_sequence_rejected(db_session):
    _make_task(db_session)
    db_session.add(TaskStep(
        task_id="task-abc", sequence=0, event_type="text",
        event_data={}, timestamp=datetime.now(timezone.utc),
    ))
    db_session.commit()
    db_session.add(TaskStep(
        task_id="task-abc", sequence=0, event_type="text",
        event_data={}, timestamp=datetime.now(timezone.utc),
    ))
    with pytest.raises(Exception):  # unique (task_id, sequence) violation
        db_session.commit()


def test_same_sequence_different_tasks_ok(db_session):
    _make_task(db_session)
    other = Task(id="task-xyz", title="t2", prompt="p", status=TaskStatus.COMPLETED)
    db_session.add(other)
    db_session.commit()
    db_session.add(TaskStep(
        task_id="task-abc", sequence=0, event_type="text",
        event_data={}, timestamp=datetime.now(timezone.utc),
    ))
    db_session.add(TaskStep(
        task_id="task-xyz", sequence=0, event_type="text",
        event_data={}, timestamp=datetime.now(timezone.utc),
    ))
    db_session.commit()  # no conflict — sequence is unique per task
    assert db_session.query(TaskStep).count() == 2
