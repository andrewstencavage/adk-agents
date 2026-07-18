# ADK application boundary and specialist contracts

## Decision

Build the system as a Python application on **Google ADK 2.5.0**, installed with
the compatible-release constraint `google-adk~=2.5.0`.  Lock the resolved build
environment and dependency hashes before implementation; upgrades are deliberate
compatibility work, not floating dependency updates.  ADK 2 introduced a
graph-based workflow runtime and the Task API, and its 2.x agent, event, and
session schemas are a breaking boundary from older ADK 1.x applications.

The application root is the **Manager**.  It uses ADK's Task API for every
specialist invocation: a named task has a typed input and a typed
`SpecialistResult` output.  It does not use free-form `transfer_to_agent` or let
one specialist directly invoke another.  This makes the Manager the only
component that can select a specialist and makes every handoff observable.

The Manager's static task registry contains five specialists.  A small,
deterministic `Workflow` may sequence a Manager decision, a single specialist
task, and Manager synthesis; it must not encode product policy or acquire
additional tools.  One control issue may produce multiple such executions, but
each execution has one primary specialist story.

## Application shape

```text
GitHub control issue -> Manager -> named Task -> one specialist -> SpecialistResult
                         ^                                      |
                         +---------- synthesis + status --------+

ADK Runner -> DatabaseSessionService (SQLite) -> ADK event/session history
           -> callback event sink              -> Local system record (SQLite)
           -> FileArtifactService               -> immutable evidence files
```

Use a per-installation `App`/runner configuration with:

- `DatabaseSessionService` backed by the local SQLite database for ADK session
  and event history.  Do not put task-board state in ADK session state.
- `FileArtifactService` rooted in a service-owned directory for immutable,
  versioned evidence payloads.  The **local system record** stores an artifact
  manifest (digest, logical kind, producing invocation, and ADK artifact
  version); GitHub comments link to the manifest identifier and a human-readable
  summary.
- A separate SQLite schema for operational records: polling checkpoint,
  dispatch idempotency key, invocation trace, artifact manifest, and later model
  capability assessments/outcomes.  This is complementary to, never a shadow
  replacement for, the task board.

## Typed contract common to every specialist

`SpecialistTask` receives only:

- `control_issue_ref`, `story_ref`, and `dispatch_id`;
- a bounded `objective`, `acceptance_criteria`, and `input_artifact_refs`;
- a `coding_agent_scope` when the task needs local-worktree access; and
- a `requested_by` identity and deadline/budget.

`SpecialistResult` is Pydantic/JSON-schema validated and contains:

- `status`: `completed`, `needs_revision`, `blocked`, or `failed`;
- `summary` and `next_manager_action`;
- `evidence_refs` and `artifact_refs`;
- `board_update_request` (a proposal, never an implicit board mutation);
- `scope_gap` or `escalation_reason` when applicable.

Specialists may create only artifacts and the evidence required by their own
contract.  They cannot change their own task-board status, select another model,
or expand their tool set.  The Manager validates the result schema, records the
handoff, and asks the Scrum Master to make any warranted task-board update.

## Specialist contracts and tool grants

| Specialist | Receives / returns | Permitted tools | Explicitly denied |
| --- | --- | --- | --- |
| Manager | Control issue plus structured specialist results / user-facing plan or status | Read-only control-issue and local-record queries; named Task dispatch | Shell, Git mutation, web search, direct GitHub writes, PR actions |
| Scrum Master | `SpecialistTask` describing a task-board operation / proposed transition, comment payload, evidence | Typed GitHub task-board adapter only | Shell, worktree, web search, model routing, PR approval |
| Research | Bounded research question and source/evidence limits / cited report plus uncertainty | Typed web-search adapter; artifact writer | General browser, shell, GitHub writes, credentials, code execution |
| Coding | One approved coding story and its coding-agent scope / patch, tests, command transcript, scope-gap | Isolated worktree adapter restricted by allowlisted commands and paths; artifact writer | Broad host shell, credential discovery, GitHub/PR writes, task-board writes |
| Review | Completed coding evidence and review gate version / accepted result or actionable findings | Read-only worktree inspection and allowlisted test runner; artifact writer; later, a narrowly typed PR-create adapter | Source mutation, merge, approval, deployment, task-board writes |

The exact GitHub adapter permissions and the Review agent's PR-create boundary
remain owned by their respective map tickets.  This decision supplies their
stable application seam rather than pre-deciding those protocols.

## Sessions, callbacks, and evidence

Create one ADK session for each Manager execution with a non-secret,
namespaced ID derived from the control issue and `dispatch_id`.  The control
issue and task board are authoritative for work lifecycle; the session is
execution context and replay/audit history only.  Persist no access token,
credential, raw secret, or unrestricted filesystem content in session state or
artifacts.

Install callbacks at these boundaries:

1. Before Manager and specialist execution: validate the task schema, current
   approval/state, scope, and idempotency key; reject rather than repair an
   invalid request.
2. Before every tool: enforce the specialist's allowlist and redact sensitive
   arguments from trace output.
3. After every tool: record a normalized evidence entry (tool, input digest,
   outcome, artifact references, and error class) in the local system record.
4. After a task: validate `SpecialistResult`, store produced artifacts, and emit
   a Manager-visible handoff event.
5. At invocation completion/failure: append a redacted trace summary and leave
   the dispatch idempotency key terminal only after durable result recording.

Callbacks are observability and policy enforcement points; they do not grant a
tool or silently retry a privilege failure.  Retry policy belongs to the typed
tool adapter and is reported as evidence.

## Acceptance criteria for the later build

- Dependency resolution installs ADK 2.5.x only, and an upgrade test must prove
  the task, session, event, and artifact compatibility boundary before a new
  minor line is accepted.
- The Manager can dispatch each of the five named task contracts and reject an
  unknown task or malformed result before any task-board update.
- An execution trace can show the control issue, dispatch ID, selected
  specialist, tool calls, evidence artifacts, and terminal result without
  exposing credentials.
- An attempted Research shell call, Coding GitHub write, Review merge, or
  specialist-to-specialist dispatch is denied and recorded.
- Restarting after a stored dispatch either resumes/reports its durable terminal
  result or safely rejects a duplicate; it never duplicates a side effect.

## Sources

- [Google ADK repository and 2.x overview](https://github.com/google/adk-python)
  (2.x workflow runtime and Task API; current release line at research time).
- [ADK 2.5.0 release](https://github.com/google/adk-python/releases/tag/v2.5.0)
  (the exact release pinned by this decision, published 2026-07-16).
- [ADK 2.0 release notes](https://github.com/google/adk-python/releases/tag/v2.0.0)
  (GA workflow and dynamic task-delegation foundations).
- [ADK base-agent callback contract](https://github.com/google/adk-python/blob/main/src/google/adk/agents/base_agent.py)
  (agent callbacks and their execution semantics).
- [ADK project architecture notes](https://github.com/google/adk-python/blob/main/AGENTS.md)
  (Runner, session, workflow, and context responsibilities).

## Open implementation checks

Before the build begins, run a narrow ADK 2.5.x spike that creates a task,
persists/reloads an SQLite session, writes/reads one artifact, and exercises a
tool-denial callback.  This is verification of the chosen version's concrete
Python signatures, not a reopening of the application boundary.
