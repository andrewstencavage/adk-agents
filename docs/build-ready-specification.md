# Build-ready specification: local ADK coding-agent system

## Purpose

Build one single-machine, local-LLM coding-agent service using Google ADK. The **Manager** is the root agent; its four specialists are **Scrum Master**, **Research**, **Coding**, and **Review**. GitHub Issues plus one GitHub Project are the control surface; there is no user-facing chat or web UI.

The service may research, prepare code changes, review them, and create a normal pull request. It must never approve, merge, deploy, or autonomously move a blocked story to `Ready` or `Done`. The user is the sole PR approver and authority to unblock work.

## Architecture and contracts

Use Python with `google-adk~=2.5.0`, with resolved dependencies and hashes locked. The ADK application root is the Manager, which uses ADK's Task API only. Its static registry contains the four specialists; specialists cannot invoke one another or acquire tools. Only the Scrum Master may request task-board changes, through its validated typed adapter.

`SpecialistTask` includes only control-issue/story references, dispatch ID, bounded objective and acceptance criteria, input artifacts, requester identity, deadline/budget, and (only for Coding) approved scope. Each Pydantic/JSON-schema-validated `SpecialistResult` returns status, summary, next Manager action, artifacts/evidence, proposed board update, and any scope gap/escalation.

Use a non-secret ADK session ID derived from the control issue and dispatch ID. Sessions are execution history, never a task board. Persist ADK sessions/events plus a separate local SQLite operational record; write immutable evidence through a service-owned `FileArtifactService`.

| Specialist | May do | Must not do |
| --- | --- | --- |
| Manager | Read control issues/local records; dispatch named task; synthesize | Shell, Git mutation, web search, direct GitHub/PR action |
| Scrum Master | Typed GitHub task-board adapter | Shell, worktrees, web search, routing, PR approval |
| Research | Typed DuckDuckGo Python-client adapter; evidence writing | Browser, shell, code execution, GitHub writes, credentials |
| Coding | One isolated worktree; approved paths/commands; artifacts | Git/GitHub writes, network, credentials, broad shell |
| Review | Read-only checkout; approved checks; artifacts; narrow accepted-PR creation | Source mutation, approval, merge, deployment, board writes |

Callbacks validate schema, current approval/state, scope, and idempotency before execution. They enforce tool allowlists and redact sensitive arguments before tools; record normalized evidence afterward; and validate/persist task handoffs. Policy failures are denied rather than silently repaired or retried.

## GitHub control plane

Use a repository-scoped fine-grained PAT, held only by the Scrum Master adapter, with Issues read/write and Project-owner Projects read/write permission. A separate host-held publication credential has only Contents write and Pull requests write; Coding receives neither.

Pin the Project node ID, owner, field IDs, and option IDs in service configuration. Configure Project Status as `Backlog -> Ready -> In Progress -> Review -> Done`, with a `Blocked` transition from In Progress or Review. The Project also has user-owned `Primary specialist` and adapter-owned `Dispatch ID` and `Agent summary` fields. A managed story is an open repository Issue, a Project item, and has the `adk:story` label; the user selects (or confirms a Manager proposal for) its Primary specialist before setting it to Ready.

`Ready` is the only dispatch signal, but dispatch also requires all of those managed-story conditions. A single SQLite-leased poller records intent, claims Ready through a structured append-only Issue comment and dispatch ID, transitions to In Progress, and performs read-after-write reconciliation. All writes are serialized and idempotent; current GitHub state is checked before external side effects. The adapter must never write `Ready` or `Done`.

Serialize adapter requests through one queue and do not start new claims while rate-limited. Honor `Retry-After`; when the primary budget is exhausted, wait for `X-RateLimit-Reset`; use bounded exponential backoff for secondary limits. Authentication or permission failure degrades service health and creates one deduplicated Blocked event for affected stories. Unsafe claims are recorded but not dispatched. GitHub remains authoritative for lifecycle and status.

## Local models and research

Support configured loopback-only Ollama and LM Studio runtimes. Discover models through native inventory APIs and invoke through ADK's LiteLLM path. Do not scan ports, download/load models, log secrets, or use cloud fallback.

A model is role-eligible only after a versioned assessment passes for its exact runtime/model fingerprint. Universal gates require bounded response, schema validation, exactly one valid typed tool call, scope-circumvention refusal, and three clean runs. Thresholds: Manager 90%; Scrum Master 95%; Research 85% with no invented citations; Coding 80% fixture tests and no scope violations; Review 85% weighted recall, 90% precision, and no critical false accept.

Rank eligible candidates by role-suite score (60%), recent same-role success (25%), and warm latency (15%). Honor a user model-selection override only when it names an eligible model, and record the override indicator with the assessment, ranking, selection, and outcome evidence. If no model is eligible, block the story visibly. Before-output runtime failure may try one next candidate only when the user did not pin a model.

The initial target-host routing set is empty: its three Ollama generative candidates have not passed the production ADK/LiteLLM suites, while the LM Studio inventory model is embedding-only. Run the versioned capability suites before the first specialist dispatch. Thereafter, route only using the most recent passing assessment for the exact fingerprint and current suite version, with the configured inventory-freshness check before dispatch. Research uses a bounded DuckDuckGo Python-client adapter, returning cited claims, evidence references, and uncertainty. Its rate-limit retry is bounded and configurable; exhaustion is reported, not routed to another provider.

## Coding, review, and PR workflow

For each coding story, create a fresh worktree from protected `main` on `agent/<issue>-<slug>`. Coding uses only the Python command profile—approved project commands such as `uv`, `pytest`, `ruff`, and type checks—approved paths, and no package installation, network, or Git. Scope gaps require Manager escalation, user approval, and a Scrum Master-recorded expansion.

A constrained host adapter verifies the accepted diff and checks before each commit. Review uses a separate read-only checkout. Blocking findings state severity, path evidence, violated requirement, and remediation. The versioned Review gate blocks unmet criteria, failed checks, scope violations, and unapproved security/dependency/CI changes; style suggestions do not block.

Allow at most two correction cycles. Review may rerun one apparently transient test failure; a repeat blocks the story. Accepted work gets a normal PR to `main` with story, commits, checks, review-gate version, and revision count. Only the user approves and merges. Remove terminal worktrees; retain unmerged remote branches for 14 days.

## Local system record and operations

Each installation has one SQLite local system record with WAL mode, foreign keys, UTC RFC 3339 timestamps, and UUIDv7 IDs. It includes migration, polling checkpoint, dispatch, invocation trace, model assessment/outcome, artifact manifest, append-only evidence ledger, and cleanup tables. It may cache enough GitHub state to prevent duplicate work but never replaces Project lifecycle.

Artifacts are immutable SHA-256-addressed files outside worktrees. The ledger stores redacted digests, action/error classes, timestamps, identities, and artifact references—not raw prompts, credentials, tool arguments, or duplicate payloads. Migrations are numbered, forward-only, and transactional. A destructive migration needs a verified backup plus user approval in the control issue.

Retain ordinary evidence 90 days, PR/Blocked evidence 180 days, and migration history/aggregate model metrics indefinitely. Daily cleanup puts eligible unreferenced artifacts in a seven-day quarantine before removal and records a redacted summary.

Run as a dedicated non-login Linux service account under systemd. Restart after failure with a 10-second delay, stopping automatic restarts after three failures in ten minutes. Rotate journals at 30 days or 512 MB. Back up the database and artifact-manifest metadata daily to a physically controlled external drive; intentionally unencrypted backups retain 14 daily sets and one monthly set for 12 months. Verify monthly with an isolated restore and SQLite checks.

After three consecutive service, backup, or recovery failures, create/update one GitHub incident issue with redacted evidence. Record recovery and close it after 24 healthy hours. Do not post routine weekly health summaries.

## Delivery acceptance

- Dependency locking keeps ADK on 2.5.x, with an upgrade compatibility test.
- Each contract dispatches valid work, denies prohibited actions, and creates a redacted durable trace.
- `Ready` claim/reconciliation survives restart and cannot duplicate effects.
- A current passing exact-fingerprint assessment and the configured inventory-freshness check precede every specialist dispatch.
- Isolated Coding/Review boundaries enforce the two-cycle gate; no agent approves, merges, or deploys.
- Migration, artifact immutability, cleanup, backup restore, and incident escalation work without exposing secrets or raw prompts.

## Supporting decision and research records

- [ADK application boundary and specialist contracts](research/adk-application-boundary-and-specialist-contracts.md)
- [GitHub polling and task-board integration protocol](research/github-polling-and-task-board-integration-protocol.md)
- [Local LLM runtime discovery and model routing](research/local-llm-runtime-discovery-and-model-routing.md)
- [Isolated coding, review, and pull-request workflow](research/isolated-coding-review-pr-workflow.md)
- [Versioned operational record and evidence schemas](research/versioned-operational-record-and-evidence-schemas.md)
- [Runtime bootstrap and assessment evidence](target-host-bootstrap.md)
- Operational policy vocabulary: [CONTEXT.md](../CONTEXT.md)
