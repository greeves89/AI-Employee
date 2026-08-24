# Epic Template — Agent-Sliced

```markdown
# Epic: <outcome-oriented name, e.g. "Self-service password reset">

Labels: epic

## Goal
What user-visible outcome this epic delivers and why it matters.
The epic itself is NEVER assigned to an agent — only its slices are.

## Shared Constraints (stated once, linked from every slice)
- Stack / architecture rules: ...
- Security rules: ...
- Conventions: ...

## Slices (vertical — each independently mergeable and verifiable)
Order = dependency order. Each slice becomes one agent-ready issue
that passes the Definition of Ready.

| # | Slice (user-observable value) | Depends on | Issue |
|---|---|---|---|
| 1 | <e.g. request-reset endpoint sends tokenized mail> | — | #... |
| 2 | <e.g. reset form consumes token, sets password> | 1 | #... |
| 3 | <e.g. rate limiting + audit log on both endpoints> | 1, 2 | #... |

## Done When
- [ ] All slices merged
- [ ] End-to-end verification: <command or manual walkthrough>
- [ ] Docs/changelog updated

## Explicitly Not in This Epic
- ...
```

## Slicing Rules

1. **Vertical, not horizontal.** A slice crosses all layers (DB → API → UI)
   and delivers observable value. "DB ticket + API ticket + UI ticket"
   forces agents to integrate blind against unwritten code.
2. **One PR per slice.** If a slice needs multiple PRs, split it again.
3. **Dependencies explicit.** An agent must be able to tell from the table
   whether its slice is unblocked.
4. **Constraints by reference.** Slices link to the epic's shared
   constraints instead of copying them — one source of truth.
