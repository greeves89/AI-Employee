---
name: writing-agent-ready-issues
description: Use when creating, triaging, or refining issues, epics, or backlog items that an autonomous AI agent will implement — e.g. when an issue is vague ("improve X"), lacks acceptance criteria, mixes several changes, or when a product owner needs to break an epic into agent-sized tasks or decide what to delegate to an agent vs. keep with a human.
---

# Writing Agent-Ready Issues

## Overview

An autonomous agent has no standup, no hallway conversation, and no chance to ask
the reporter a follow-up question mid-task. **The issue IS the entire onboarding.**
Write every issue like a brief for a competent new hire on day one: what is the
problem, what does "good" look like, what does "bad" look like, and what should
they do when unsure.

Evidence: concise, well-scoped, self-contained, context-guided issues raise
agent PR merge rates by up to 30 percentage points.

## When to Use

- Before assigning any issue to an automated agent
- When a product owner triages an inbox of raw requests, bug reports, or ideas
- When breaking an epic into implementable slices
- When an agent's PR missed the point — the issue was probably not agent-ready

**When NOT to use:** quick interactive pairing sessions where a human steers the
agent turn by turn; there the conversation replaces the brief.

## Triage: Every Issue Gets Exactly One State

Move each incoming item through this state machine. One category label
(`bug` | `enhancement`) plus one state label — never more, never zero:

| State | Meaning | Exit condition |
|---|---|---|
| `needs-triage` | Untouched inbox item | PO decision made |
| `needs-info` | Cannot be specified yet | Reporter answered the concrete questions asked in a comment |
| `ready-for-agent` | Passes the Definition of Ready below | Agent assigned |
| `ready-for-human` | Specified, but needs human judgment (design taste, production risk, manual testing) | Human picks it up |
| `wontfix` / closed | Duplicate, already implemented, or rejected | Reason recorded in a closing comment |

Decision rules during triage:

1. **Verify before you brief.** Reproduce the bug from the reporter's steps.
   Search the codebase by *concept*, not just keyword — the feature may already
   exist. Already implemented → close with a pointer to where. No codebase
   access at triage time → the issue may be drafted but stays out of
   `ready-for-agent` until someone verified it against the code.
2. **Ask, don't guess.** Missing *reporter facts* (repro steps, environment,
   data volume) → `needs-info` with concrete, answerable questions. Missing
   *product decisions* ("maybe dark mode?") are the PO's to make — the item
   stays `needs-triage` until decided; never bounce product questions to the
   reporter. Never pad either gap with assumptions.
3. **Split before assigning.** More than one PR needed? Two cases:
   - Unrelated asks bundled in one item (a grab-bag) → split into independent
     issues, close the original with a comment linking the children
   - One coherent goal that spans several PRs → it is an epic, see below
4. **Category tie-breaker:** a regression from previously working/acceptable
   behavior is a `bug`; a new capability or a new quality target is an
   `enhancement`.

## Definition of Ready (for Agents)

An issue is `ready-for-agent` only if ALL of these hold:

- [ ] **One paragraph problem/goal** — what and why, in plain language
- [ ] **Verifiable acceptance criteria** — each criterion checkable by a
      command, test, or observable behavior. "Looks good" is not a criterion.
- [ ] **Explicit scope boundary** — an "Out of scope" list. No boundary =
      guaranteed scope creep.
- [ ] **Durable context pointers** — name modules, symbols, domain concepts,
      and behavioral contracts ("`TaskScheduler` must keep firing for stopped
      agents"). NOT file paths + line numbers — those rot within weeks.
- [ ] **Verification recipe** — the exact commands to run (tests, build, lint)
      and what output counts as success. Cannot name the commands? Look them up
      (README, CI config) — placeholders like `<test command>` fail this check.
- [ ] **Escalation rule** — what the agent should do when unsure. Default:
      touches security or data visibility → stop and comment with the concrete
      question; anything else → take the conservative option and flag the
      decision in the PR description.
- [ ] **One-PR sized** — result reviewable in one sitting

Use the full section layout from [templates/issue-template.md](templates/issue-template.md).

## Epics: Vertical Slices, Not Layers

The PO keeps the intent at epic level; agents never work "on the epic".

1. Write the epic as **goal + slice list** — see
   [templates/epic-template.md](templates/epic-template.md)
2. Slice **vertically** (each slice delivers observable user value and is
   independently mergeable), not horizontally (DB ticket, API ticket, UI
   ticket — those force agents to integrate blind)
3. Make **dependency order explicit** ("slice 3 requires slice 1 merged")
4. State **shared constraints once** in the epic (stack, security rules,
   conventions) and link every slice back to it — do not copy-paste them
5. Each slice becomes a normal issue that must pass the Definition of Ready

## Delegate to Agent vs. Keep Human

| Delegate to agent | Keep with a human |
|---|---|
| Bug fixes with reproduction steps | Production incidents, hotfixes under pressure |
| Test coverage, documentation, accessibility passes | Security- and auth-critical changes (agent may draft, human must own) |
| Mechanical refactors, dependency bumps | Cross-repo or deep-domain redesigns |
| Well-specified feature slices | Ambiguous, exploratory, or taste-driven work |

## Common Mistakes

| Mistake | Fix |
|---|---|
| "Improve performance of X" | Measure the baseline first, then name the target: "list endpoint p95 < 300ms at 10k rows (baseline: 1.4s)" — never invent numbers |
| Acceptance criteria only a human can judge | Rewrite as command + expected output |
| File paths and line numbers as context | Name symbols and behavioral contracts instead |
| Feature + refactor in one issue | Two issues; refactor first, feature depends on it |
| Requirements hidden in comment threads | Fold decisions back into the issue body before assigning |
| No "Out of scope" section | Agent "helpfully" rewrites neighboring code — add the boundary |
| No verification recipe | Agent declares success without proof — list the commands |
| Assigning the epic itself | Slice first; agents get slices only |

## Quick Reference — Issue Skeleton

```
Title: <verb + object, one outcome>
1. Context      — why this exists, links to epic/constraints
2. Problem/Goal — one paragraph
3. Scope        — In / Out (explicit non-goals)
4. Acceptance criteria — verifiable, checkbox list
5. Context pointers    — modules, symbols, contracts, docs
6. Verification        — exact commands + expected result
7. Constraints         — security, style, compatibility
8. When unsure         — escalation rule
```
