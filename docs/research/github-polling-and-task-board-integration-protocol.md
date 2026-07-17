# GitHub polling and task-board integration protocol

## Decision

GitHub Issues plus one owner-scoped GitHub Project are the task board and the
only user-visible workflow authority.  The local service polls that Project;
it does not accept a chat command, filesystem flag, label, or issue comment as
approval to dispatch.  **A story is dispatchable only while its Project Status
is `Ready`.**

The target-host service uses one fine-grained personal access token (PAT),
restricted to the target repository, held only by the GitHub adapter's secret
configuration.  The Manager and all specialists receive typed GitHub operations
instead of the token, raw `gh` access, or a general HTTP client.

## Least-privilege token

Create a fine-grained PAT with resource owner set to the Project owner and
repository access set to **only** the target repository.  Grant:

| Permission | Access | Why |
| --- | --- | --- |
| Repository metadata | read (automatically included) | Resolve the configured repository and API metadata. |
| Repository issues | read/write | Read stories and comments; add labels; write structured handoffs; assign/close or reopen only where the workflow permits. |
| Project owner: Projects | read/write | Read Project items and fields; update the configured Project Status and two agent-owned text fields. For an organization-owned Project, this is the organization `Projects` permission. |

Do not grant Contents, Pull requests, Actions, Workflows, Administration,
Secrets, Deployments, Commit statuses, Discussions, or organization membership
permissions to this token.  Coding and Review use separate, later-defined
capability adapters; they must not inherit this task-board credential.

At installation, run a non-mutating permissions probe against the configured
repository and Project and record the `X-Accepted-GitHub-Permissions` response
headers.  If a user-owned Project requires a different documented fine-grained
permission on the target GitHub plan, stop installation with that exact missing
permission; do not substitute a broad classic PAT.

## Project configuration

Configure one Project and pin its node ID, owner, and field/option IDs in the
service configuration.  Names are human-facing; IDs are the protocol.

| Field | Type | Values / ownership |
| --- | --- | --- |
| Status | single select | `Backlog`, `Ready`, `In Progress`, `Review`, `Blocked`, `Done`; lifecycle authority. |
| Primary specialist | single select | `Scrum Master`, `Research`, `Coding`, `Review`; set by a user before `Ready`, or proposed by the Manager and confirmed by the user. |
| Dispatch ID | text | Adapter-owned; current/most recent dispatch UUID. |
| Agent summary | text | Adapter-owned concise current result or block reason; no secret, raw model trace, or large artifact. |

Every managed story is a repository Issue and a Project item with the
`adk:story` label.  A user-facing request Issue may carry `adk:control`.
`adk:needs-human` marks an escalation but never changes Status itself.  Labels
classify; Project Status is the sole lifecycle and approval state.

Allowed transitions are:

```text
Backlog --(user)--> Ready --(Scrum Master claim)--> In Progress
In Progress --(Scrum Master on specialist handoff)--> Review
Review --(user approves PR)--> Done
Backlog|Ready|In Progress|Review --(Scrum Master on exception)--> Blocked
Blocked --(user only)--> Ready
```

The adapter must never write `Ready` or `Done`.  The Review agent recommends
acceptance; the user alone approves the PR and performs the final `Done`
transition.  Any direct user status change wins over a stale agent attempt.

## Polling and dispatch checkpoint

Polling is intentional for this single-machine release, despite GitHub's
general preference for webhooks.  Run one target-host polling worker, protected
by a renewable SQLite lease; a second service process becomes passive rather
than issuing concurrent reads or writes.

Every 60 seconds plus deterministic jitter, and once at startup, the worker:

1. Pages through the configured Project's items via GraphQL, including Project
   item ID, Issue node ID/number, Status option ID, item `updatedAt`, labels,
   and the two adapter-owned fields.
2. Keeps only open `adk:story` Issues whose current Status option ID is the
   configured `Ready` option.  It re-fetches the issue/comments only for these
   candidates.
3. In one SQLite transaction, creates a `dispatch` row with a unique key
   `(project_id, project_item_id, ready_generation)`.  `ready_generation`
   increments only when the persisted observation changes from non-Ready to
   Ready.  The row records source status version, issue update timestamp,
   idempotency key, and desired transition.
4. Immediately re-reads the Project item.  If it is no longer `Ready`, marks
   the row superseded and does nothing else.  Otherwise writes the claim event
   and changes the Status to `In Progress`.
5. Re-reads the item after mutation.  It dispatches the specialist only when
   the observed Status is `In Progress` and the persisted Dispatch ID equals
   the local row's ID.

No cursor is treated as an authority boundary: the Project is fully paginated
each cycle because a `Ready` change can occur anywhere in its ordering.  The
checkpoint lowers repeated work, while the current Project state decides
eligibility after every restart.

## Idempotent writes and structured comments

GitHub API mutations do not provide server-side idempotency keys for this
workflow.  The adapter therefore persists its write intent before the request,
uses the dispatch UUID as `clientMutationId` where GraphQL accepts it, and
performs a read-after-write reconciliation.  A retry always reuses the same
dispatch and event IDs; it never creates a new dispatch because an HTTP response
was lost.

Every durable agent handoff is one append-only Issue comment containing this
machine-readable envelope followed by a short human summary:

```markdown
<!-- adk-event:v1
{"event_id":"<uuidv7>","dispatch_id":"<uuidv7>","kind":"dispatch.claimed","occurred_at":"<RFC3339 UTC>","schema_version":1,"payload":{"project_item_id":"<node-id>","status":"In Progress"}}
-->
## Agent update · In Progress

<concise summary and artifact links>
```

Permitted `kind` values are `dispatch.claimed`, `specialist.handoff`,
`review.findings`, `story.blocked`, and `story.completed`.  The parser accepts
only the exact `adk-event:v1` prefix, validates the JSON schema and UUIDs, and
ignores malformed or user-authored comments.  It stores the comment ID and
body digest in SQLite.  Before retrying a comment write, it looks for the same
`event_id`; a matching envelope is success, a conflicting payload is a fatal
integrity error.  The service never records a token, prompt, raw tool input,
or unredacted log in a comment.

For a claim, write the event comment first, set `Dispatch ID`, then set Status.
If the process fails between steps, reconciliation either completes the same
intent or leaves the story visibly `Ready`/`Blocked` with an explicit recovery
comment.  A specialist starts only after the final read confirms all three
claim markers.  This makes duplicate side effects less likely than a best-effort
status update and makes recovery inspectable on GitHub.

## Read/write adapter surface

The Scrum Master is the sole caller of this adapter.  It exposes only:

- `list_ready_stories()`, `get_story()`, and `get_agent_events()`;
- `claim_ready_story(dispatch_id)`;
- `record_handoff(event)`, `move_to_review(dispatch_id)`, and
  `block_story(event)`; and
- `record_user_completion_observed()`.

Each method validates owner/repository/Project IDs, known field IDs, transition
preconditions, label allowlists, comment schema, and the SQLite dispatch lease.
It cannot create a Project, alter fields/options, change repository settings,
merge a PR, or write a `Ready`/`Done` status.  The Manager asks for a typed
operation; it does not call GitHub itself.

## Rate limits and failure behavior

Serialize all requests through one queue.  Paginate using GitHub Link cursors;
avoid concurrent fetches; pause at least one second between mutating requests.
Capture REST/GraphQL rate headers and GraphQL cost in the local system record.
On `Retry-After`, wait exactly as directed; when the remaining primary budget is
zero, wait until `X-RateLimit-Reset`; otherwise use bounded exponential backoff
starting at one minute for secondary-rate-limit errors.  Do not start a new
claim while the queue is rate-limited.  Authentication or permission failure
sets the service health to degraded and creates one deduplicated `Blocked`
event for affected stories.

## Acceptance criteria

- Only an open `adk:story` in current Project Status `Ready` can cause a
  specialist dispatch; a label or comment alone cannot.
- Restart during any claim step converges to one dispatch, one claim event, and
  either confirmed `In Progress` or a visible blocked/recovery state.
- A user movement away from `Ready` before claim prevents execution; agents
  never write `Ready` or `Done`.
- Every agent state change is both readable by a human and schema-validated by
  the parser, without exposing credentials or raw prompts.
- The configured token cannot read repository contents or mutate PRs, and a
  missing Project permission fails closed at installation.

## Sources

- [GitHub fine-grained PAT permissions](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)
  — repository Issues and organization Projects permission requirements.
- [Managing Projects by API](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects)
  and [Project GraphQL mutations](https://docs.github.com/en/graphql/reference/projects)
  — field-value mutations and their limits.
- [Issue-comment API](https://docs.github.com/en/rest/issues/comments) — Issues
  write permission and comment rate-limit implications.
- [GitHub REST best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)
  and [rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
  — serialized requests, mutation pacing, and retry behavior.
