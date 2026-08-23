# Issue Template — Agent-Ready Task

```markdown
# <Verb + object — one outcome, e.g. "Fix scheduler skipping stopped agents">

Labels: <bug | enhancement> + ready-for-agent
Epic: #<epic-number> (if part of one)

## 1. Context
Why this task exists. One or two sentences plus links:
- Epic / parent issue: #...
- Related decisions or docs: ...

## 2. Problem / Goal
One paragraph. What is broken or missing, and what the world looks like
when this is done. For bugs: exact reproduction steps and observed vs.
expected behavior.

## 3. Scope
**In scope:**
- ...

**Out of scope (do NOT touch):**
- ...

## 4. Acceptance Criteria
Every criterion must be checkable by a command, a test, or an observable
behavior — never by taste.

- [ ] Given <precondition>, when <action>, then <observable result>
- [ ] New/changed behavior is covered by tests (state which kind)
- [ ] Existing test suite stays green

## 5. Context Pointers
Durable references — symbols, modules, contracts, domain concepts.
No line numbers (they rot).

- Entry point: `<module or symbol>`
- Behavioral contract that must not break: "<e.g. API stays backward
  compatible for clients sending the old field>"
- Similar existing pattern to follow: `<symbol or feature>`

## 6. Verification
Exact commands the agent must run, and what success looks like:

```bash
<test command>        # expected: all pass
<build/lint command>  # expected: exit 0
```

Manual check (if any): <steps + expected observation>

## 7. Constraints
- Security: <e.g. endpoint requires auth + ownership check + test>
- Conventions: <e.g. follow existing service pattern, no new dependencies
  without approval>
- Compatibility: <e.g. migration must be reversible>

## 8. When Unsure
Default rule (adapt only if this task needs something stricter):
- The doubt touches security or data visibility → STOP and comment on
  the issue with the concrete question
- Anything else → choose the conservative option (smaller scope, reuse
  existing code) and flag the decision in the PR description
```
