"""Agent TODO API - persistent task tracking for agents."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth, require_auth_or_agent, verify_agent_token
from app.models.agent_todo import AgentTodo, TodoStatus
from app.schemas.todo import (
    TodoCreate,
    TodoUpdate,
    TodoBulkUpdate,
    TodoListResponse,
    TodoResponse,
)

router = APIRouter(prefix="/todos", tags=["todos"])


def _to_response(t: AgentTodo) -> dict:
    return {
        "id": t.id,
        "agent_id": t.agent_id,
        "task_id": t.task_id,
        "project": t.project,
        "project_path": t.project_path,
        "title": t.title,
        "description": t.description,
        "status": t.status.value if isinstance(t.status, TodoStatus) else t.status,
        "priority": t.priority,
        "sort_order": t.sort_order,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "completed_at": t.completed_at,
    }


def _count_by_status(todos: list[AgentTodo]) -> dict:
    counts = {"pending": 0, "in_progress": 0, "completed": 0}
    for t in todos:
        s = t.status.value if isinstance(t.status, TodoStatus) else t.status
        if s in counts:
            counts[s] += 1
    return counts


# --- UI-facing endpoints (require user auth) ---


async def _assert_agent_visible(agent_id: str, user, db) -> None:
    """404 unless the caller owns/shares the agent (agent principal → only itself,
    admin → any). TODOs can be fed back into agent context, so foreign read+write denied."""
    from app.dependencies import is_agent_principal
    from app.core.ownership import visible_agent_ids
    if is_agent_principal(user):
        if user.id != agent_id:
            raise HTTPException(status_code=404, detail="Todo not found")
        return
    vids = await visible_agent_ids(user, db)
    if vids is not None and agent_id not in vids:
        raise HTTPException(status_code=404, detail="Todo not found")


@router.get("/agents/{agent_id}", response_model=TodoListResponse)
async def list_todos_ui(
    agent_id: str,
    status: str | None = Query(None),
    task_id: str | None = Query(None),
    project: str | None = Query(None),
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """List TODOs for an agent (frontend Todo tab)."""
    await _assert_agent_visible(agent_id, user, db)
    query = select(AgentTodo).where(AgentTodo.agent_id == agent_id)
    if status:
        query = query.where(AgentTodo.status == TodoStatus(status))
    if task_id is not None:
        query = query.where(AgentTodo.task_id == task_id)
    if project is not None:
        query = query.where(AgentTodo.project == project)
    query = query.order_by(AgentTodo.priority.asc(), AgentTodo.sort_order.asc(), AgentTodo.created_at.asc())
    result = await db.execute(query)
    todos = list(result.scalars().all())
    counts = _count_by_status(todos)
    # Collect unique project names
    projects = sorted({t.project for t in todos if t.project})
    return {
        "todos": [_to_response(t) for t in todos],
        "total": len(todos),
        "projects": projects,
        **counts,
    }


@router.post("/agents/{agent_id}", response_model=TodoResponse)
async def create_todo_ui(
    agent_id: str,
    body: TodoCreate,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Create a TODO from the UI."""
    await _assert_agent_visible(agent_id, user, db)
    todo = AgentTodo(
        agent_id=agent_id,
        task_id=body.task_id,
        project=body.project,
        project_path=body.project_path,
        title=body.title,
        description=body.description,
        priority=body.priority,
        sort_order=body.sort_order,
    )
    db.add(todo)
    await db.commit()
    await db.refresh(todo)
    return _to_response(todo)


@router.patch("/{todo_id}", response_model=TodoResponse)
async def update_todo(
    todo_id: int,
    body: TodoUpdate,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Update a single TODO (from UI)."""
    result = await db.execute(select(AgentTodo).where(AgentTodo.id == todo_id))
    todo = result.scalar_one_or_none()
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    await _assert_agent_visible(todo.agent_id, user, db)

    if body.title is not None:
        todo.title = body.title
    if body.description is not None:
        todo.description = body.description
    if body.project is not None:
        todo.project = body.project
    if body.project_path is not None:
        todo.project_path = body.project_path
    if body.status is not None:
        todo.status = TodoStatus(body.status)
        if body.status == "completed":
            todo.completed_at = datetime.now(timezone.utc)
        elif body.status == "pending":
            todo.completed_at = None
    if body.priority is not None:
        todo.priority = body.priority
    if body.sort_order is not None:
        todo.sort_order = body.sort_order

    await db.commit()
    await db.refresh(todo)
    return _to_response(todo)


@router.delete("/{todo_id}")
async def delete_todo(
    todo_id: int,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Delete a TODO."""
    result = await db.execute(select(AgentTodo).where(AgentTodo.id == todo_id))
    todo = result.scalar_one_or_none()
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    await _assert_agent_visible(todo.agent_id, user, db)
    await db.delete(todo)
    await db.commit()
    return {"deleted": todo_id}


# --- Agent-facing endpoints (require agent token) ---


@router.get("/agent/list")
async def list_todos_agent(
    status: str | None = Query(None),
    task_id: str | None = Query(None),
    project: str | None = Query(None),
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """List TODOs for the calling agent (MCP tool: list_todos)."""
    agent_id = auth["agent_id"]
    query = select(AgentTodo).where(AgentTodo.agent_id == agent_id)
    if status:
        query = query.where(AgentTodo.status == TodoStatus(status))
    if task_id is not None:
        query = query.where(AgentTodo.task_id == task_id)
    if project is not None:
        query = query.where(AgentTodo.project == project)
    query = query.order_by(AgentTodo.priority.asc(), AgentTodo.sort_order.asc())
    result = await db.execute(query)
    todos = list(result.scalars().all())
    counts = _count_by_status(todos)
    projects = sorted({t.project for t in todos if t.project})
    return {
        "todos": [_to_response(t) for t in todos],
        "total": len(todos),
        "projects": projects,
        **counts,
    }


@router.put("/agent/bulk")
async def bulk_update_todos(
    body: TodoBulkUpdate,
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Bulk-update TODOs for the calling agent (MCP tool: update_todos).

    Smart merge strategy:
    - Existing TODOs with matching title → update status/priority/description
    - New TODOs (no title match) → add them
    - Existing TODOs NOT in the new list → keep them (never auto-delete!)
    - Completed TODOs are always preserved
    """
    agent_id = auth["agent_id"]

    # Resolve project from body-level or per-item level
    bulk_project = body.project
    bulk_project_path = body.project_path

    # Find existing TODOs for this scope.
    #
    # The scope has to come from the SAME fields the write path below uses
    # (`item.project or bulk_project`), not from the body alone (#1128). The
    # schema allows the project per item, and for a list spanning several
    # projects that is the only way to pass it. Scoping by `body.project`
    # only meant such a call looked for existing TODOs among those WITHOUT a
    # project — where a TODO that HAS one can never be found. Every title
    # counted as new, so the call reliably produced duplicates.
    item_projects = {item.project or bulk_project for item in body.todos}
    named_projects = {p for p in item_projects if p is not None}
    unscoped = and_(AgentTodo.task_id.is_(None), AgentTodo.project.is_(None))

    existing_query = select(AgentTodo).where(AgentTodo.agent_id == agent_id)
    scope_by_project = body.task_id is None
    if body.task_id is not None:
        existing_query = existing_query.where(AgentTodo.task_id == body.task_id)
    elif named_projects and None in item_projects:
        # Mixed list: some items name a project, some do not.
        existing_query = existing_query.where(
            or_(AgentTodo.project.in_(named_projects), unscoped)
        )
    elif named_projects:
        existing_query = existing_query.where(AgentTodo.project.in_(named_projects))
    else:
        existing_query = existing_query.where(unscoped)

    result = await db.execute(existing_query)
    existing = list(result.scalars().all())

    # Build lookup for matching. Once the scope can span several projects, the
    # title alone is no longer a unique key: the same title in two projects
    # would collide and an item of project A would silently update the TODO of
    # project B. Within a task_id scope the project stays out of the key, so
    # matching there behaves exactly as before.
    def _match_key(project: str | None, title: str) -> tuple[str | None, str]:
        return (project if scope_by_project else None, title.strip().lower())

    existing_by_title: dict[tuple[str | None, str], AgentTodo] = {}
    for t in existing:
        existing_by_title[_match_key(t.project, t.title)] = t

    updated_count = 0
    added_count = 0
    max_sort = max((t.sort_order for t in existing), default=-1)

    for item in body.todos:
        # Per-item project overrides bulk-level project
        item_project = item.project or bulk_project
        item_project_path = item.project_path or bulk_project_path
        key = _match_key(item_project, item.title)
        if key in existing_by_title:
            # Update existing todo
            t = existing_by_title[key]
            if item.description is not None:
                t.description = item.description
            if item.status:
                t.status = TodoStatus(item.status)
                if item.status == "completed" and not t.completed_at:
                    t.completed_at = datetime.now(timezone.utc)
            if item.priority:
                t.priority = item.priority
            if item_project:
                t.project = item_project
            if item_project_path:
                t.project_path = item_project_path
            updated_count += 1
        else:
            # Add new todo
            max_sort += 1
            todo = AgentTodo(
                agent_id=agent_id,
                task_id=body.task_id,
                project=item_project,
                project_path=item_project_path,
                title=item.title,
                description=item.description,
                status=TodoStatus(item.status) if item.status else TodoStatus.PENDING,
                priority=item.priority or 3,
                sort_order=max_sort,
                completed_at=datetime.now(timezone.utc) if item.status == "completed" else None,
            )
            db.add(todo)
            existing.append(todo)
            # Also register it for matching: two items with the same title in
            # ONE call must not create the duplicate all over again.
            existing_by_title[key] = todo
            added_count += 1

    await db.commit()
    # Refresh all to get updated values
    for t in existing:
        await db.refresh(t)

    return {
        "updated": updated_count,
        "added": added_count,
        "total": len(existing),
        "todos": [_to_response(t) for t in sorted(existing, key=lambda x: (x.priority, x.sort_order))],
    }


@router.patch("/agent/{todo_id}/complete")
async def complete_todo_agent(
    todo_id: int,
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Mark a TODO as completed (MCP tool: complete_todo)."""
    agent_id = auth["agent_id"]
    result = await db.execute(
        select(AgentTodo).where(
            AgentTodo.id == todo_id,
            AgentTodo.agent_id == agent_id,
        )
    )
    todo = result.scalar_one_or_none()
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")

    todo.status = TodoStatus.COMPLETED
    todo.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(todo)
    return _to_response(todo)
