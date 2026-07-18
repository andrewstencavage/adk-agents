# ADK Agent System

The local-LLM Google ADK system being planned in this repository. Its vocabulary keeps planning decisions distinct from later implementation work.

## Language

**Build-ready specification**:
A decision-complete description of the agent system that can be handed to an implementer. It establishes architecture, responsibilities, operating boundaries, and acceptance criteria, but is not a working implementation.
_Avoid_: implementation, first vertical slice

**Manager**:
The root ADK agent that interprets requests, delegates bounded work, and composes the user-facing outcome. It does not own specialist tools or domain workflow.
_Avoid_: orchestrator, supervisor

**Scrum Master**:
A specialist agent responsible for the task-board workflow and its status transitions. It is a peer of other specialists, invoked by the Manager for board-specific work.
_Avoid_: board capability, manager

**Task board**:
The GitHub Issues and GitHub Project pair that represents work items and their workflow state for this system.
_Avoid_: local board, backlog file

**Model capability assessment**:
A repeatable evaluation that discovers connected local model runtimes and measures each available model against the capabilities required by an agent role.
_Avoid_: hard-coded model choice, model preference

**Model routing**:
The Manager's evidence-based selection of a connected local model for a specific task, informed by capability assessments and prior outcomes.
_Avoid_: fixed role model, manual model assignment

**Coding-agent scope**:
The explicitly granted local-worktree permissions for a coding task, including an allowlist of commands and credentials. A scope gap is escalated to the Manager; it is never worked around autonomously.
_Avoid_: agent autonomy, implicit permissions

**Story worktree**:
A fresh local Git worktree created for exactly one specialist story from a trusted base branch, using an `agent/<issue>-<slug>` branch. The Coding agent may edit and test only in this worktree; a constrained host adapter creates the commit from its verified diff.
_Avoid_: shared checkout, Coding-agent Git authority, credentialed Coding agent

**Commit authority**:
The constrained host-side capability that creates one story-branch commit from an accepted worktree diff after required tests have passed. It is distinct from Coding, Review, and human pull-request approval.
_Avoid_: Coding-agent commit, Review-agent source mutation, implicit commit

**Python command profile**:
The first predefined coding-agent scope: approved Python project commands such as `uv`, `pytest`, `ruff`, and configured type checks, with no package installation or network access during a specialist story. Other language profiles are added only through an explicit future decision.
_Avoid_: arbitrary shell, implicit multi-language support

**Scope expansion**:
The user-approved change to a specialist story's coding-agent scope when an excluded path or command is genuinely required. Coding reports the gap to the Manager; the Manager asks the user and the Scrum Master records any approved expansion before work resumes.
_Avoid_: self-authorized exception, silent scope drift

**Blocked story**:
A task-board work item paused by the Scrum Master because an authority or scope decision requires the user's approval.
_Avoid_: failed task, skipped story

**Web-search adapter**:
The narrowly scoped service behind the Research agent's typed search tool. The initial adapter uses a DuckDuckGo Python client and may be replaced without changing the agent contract.
_Avoid_: general browser access, shared web tool

**Research retry policy**:
The bounded, configurable delay-and-retry behavior used when the web-search adapter is rate-limited. It prioritizes eventual completion over response speed and reports exhaustion explicitly.
_Avoid_: provider fallback, unbounded retry

**Task-board workflow**:
The Scrum Master's GitHub Project state sequence: Backlog, Ready, In Progress, Review, Blocked, and Done. Only the user may move a blocked item to Ready or Done.
_Avoid_: implicit task status, self-unblocking

**Specialist story**:
A task-board work item with exactly one primary specialist owner. Cross-specialist work is expressed as linked stories with an explicit handoff artifact.
_Avoid_: multi-owner story, combined research-and-coding task

**Human review**:
The user's sole authority to approve a normal, ready-for-review pull request after the Review agent has accepted it.
_Avoid_: automated approval, agent approval

**Review agent**:
A specialist that evaluates a completed coding story from an independent read-only checkout of its committed branch. It may run the Python command profile, returns actionable findings to the Coding agent when changes are needed, and opens a pull request only after accepting the result.
_Avoid_: human approver, coding agent

**Review loop**:
The bounded Coding-agent and Review-agent handoff cycle that continues until the Review agent accepts the output or the Scrum Master blocks the story for the user.
_Avoid_: unbounded autonomous iteration, self-approval

**Review gate**:
The Review agent's versioned acceptance checklist. It blocks acceptance for unmet story acceptance criteria, failing approved tests/lint/type checks, a scope violation, or an unapproved security, dependency, or CI-workflow change; ordinary style suggestions are non-blocking.
_Avoid_: style-only review, human approval

**Review finding**:
An actionable Review-agent result that names severity, file/path evidence, the violated requirement, and a concrete remediation. A finding is the only reason for a Coding-agent revision cycle.
_Avoid_: vague feedback, stylistic preference without requirement

**Transient test retry**:
One automatic rerun of an approved test command by Review when its failure appears to be infrastructure-related, such as a timeout, unavailable local service, or runner error. A repeated failure blocks the story rather than consuming a revision cycle.
_Avoid_: unbounded retry, treating infrastructure noise as a code finding

**Revision audit trail**:
The ordered record of a story's host-created initial and corrective commits, Review findings, and structured story updates. It preserves correction history; any squash is the user's later merge choice.
_Avoid_: amended-away review history, invisible correction cycle

**Story update**:
A structured GitHub Issue comment for a specialist-story handoff. It includes branch and commit SHA, changed-file summary, approved command results, review-cycle count, relevant artifact links, and a concise reason whenever the story is Blocked.
_Avoid_: opaque status, raw prompts, credential-bearing logs

**Pull-request handoff**:
A normal PR created by Review only after the Review gate accepts the story. It targets protected `main` from `agent/<issue>-<slug>` and includes the story link, summary, commit SHA(s), test results, Review-gate version, and revision count. The user alone approves and merges it.
_Avoid_: draft PR after acceptance, agent approval, agent merge

**Revision limit**:
Two Coding-agent and Review-agent correction cycles per story. Further unresolved findings cause the Scrum Master to block the story for the user.
_Avoid_: infinite retry, silent abandonment

**Control issue**:
The GitHub Issue through which the user primarily requests work and receives Manager plans, specialist handoffs, and status updates.
_Avoid_: chat interface, command-line prompt

**Polling loop**:
The local, configurable scheduler that discovers eligible approved work from GitHub. It resumes safely after restart and makes GitHub writes idempotently.
_Avoid_: inbound webhook, manual-only trigger

**GitHub credential**:
A fine-grained personal access token restricted to the target repository and the minimum permissions needed for the first release's GitHub operations. A user-owned GitHub Project V2 is the narrow platform exception: GitHub does not support fine-grained tokens for that API surface, so its Project-only GraphQL operations use a separate classic token limited to the `project` scope. Issue access remains a separate repository-scoped fine-grained token.
_Avoid_: broad classic token, shared credential, using the Project token for repository Issue operations

**Change-publication credential**:
A host-held, repository-scoped fine-grained credential with Contents write and Pull requests write that pushes a commit-authority-created story branch and lets Review create a normal PR. It is never exposed to Coding and cannot approve, merge, or alter protected-branch rules.
_Avoid_: credentialed Coding agent, PR approval credential, merge credential

**Story-branch retention**:
The cleanup policy for a story branch and its worktree: remove the local worktree at terminal state, let GitHub delete a merged branch automatically, and retain an unmerged closed or blocked remote branch for 14 days before cleanup.
_Avoid_: permanent abandoned worktrees, immediate loss of blocked work

**Local system record**:
One SQLite database per service installation holding restart-safe operational data in separate polling/dispatch, model-assessment/outcome, artifact-manifest, evidence-ledger, and migration tables. It complements rather than replaces the GitHub task board, which remains authoritative for task lifecycle.
_Avoid_: GitHub-only state, ephemeral session state

**Operational migration**:
A numbered, forward-only SQLite schema change run transactionally at service startup. A destructive rewrite requires a pre-migration backup and explicit user approval.
_Avoid_: automatic destructive upgrade, undocumented schema drift

**Artifact manifest**:
The SQLite record for an immutable evidence file held in a service-owned directory and addressed by its SHA-256 digest. It stores metadata and references, never the full artifact payload.
_Avoid_: mutable evidence, blob-heavy operational database

**Evidence ledger**:
An append-only, redacted SQLite record for a meaningful agent or tool event. It contains dispatch and invocation IDs, action type, input/output digests, outcome or error class, timestamp, and artifact-manifest reference, but no raw secret or duplicated payload.
_Avoid_: mutable audit trail, secret-bearing event log

**Operational retention**:
The automatic retention policy for the local system record: retain ordinary invocation traces, evidence-ledger rows, and artifacts for 90 days; retain PR- or Blocked-story evidence for 180 days; retain migration history and aggregated model-assessment/outcome metrics indefinitely.
_Avoid_: manual cleanup dependency, unbounded routine storage

**Cleanup run**:
A daily automatic maintenance operation that removes only expired, unreferenced routine evidence. It moves artifact files to a seven-day local quarantine before removing their manifest and ledger rows, then records a cleanup summary event.
_Avoid_: immediate irreversible purge, silent retention cleanup

**Service supervisor**:
The systemd-managed, dedicated non-login Linux service account and unit that owns one installation's process lifecycle, filesystem access, and journal identity. It restarts on failure after 10 seconds and stops retrying after three failures within 10 minutes.
_Avoid_: interactive shell process, user-login service, container orchestrator

**External backup set**:
An unencrypted, routine copy of the local system record and artifact-manifest metadata written to the target host's physically controlled external drive. It retains daily sets for 14 days and one monthly set for 12 months before automatic cleanup.
_Avoid_: same-disk backup, permanent backup accumulation, assumed encryption

**Restore verification**:
A monthly, isolated restoration of the SQLite backup followed by integrity checks before the corresponding external backup set is reported healthy.
_Avoid_: copy-only backup success, production-database restore test

**Operational incident**:
A persistent service, backup, or recovery failure surfaced to the user through one dedicated GitHub control issue after three consecutive failed attempts, while detailed redacted evidence remains in the local journal and evidence ledger.
_Avoid_: transient-failure notification, raw-log GitHub issue, duplicate alert issues

**Incident recovery**:
The service's return to healthy operation, recorded as a redacted update on its operational incident; the incident closes automatically after 24 uninterrupted healthy hours.
_Avoid_: silently recovered incident, permanently open recovered alert

**Service journal retention**:
The service's local journal history, automatically rotated after 30 days or 512 MB, whichever limit is reached first.
_Avoid_: unbounded journal, indefinite raw operational logs

**Incident-driven observability**:
The reporting posture in which routine health remains in local metrics and logs, while GitHub receives only persistent operational incidents and their recoveries.
_Avoid_: weekly healthy-status noise, silent persistent failure
