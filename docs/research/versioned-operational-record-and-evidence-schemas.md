# Versioned operational record and evidence schemas

## Decision

Each service installation has one local SQLite database—the **local system
record**—for restart-safe operational state only.  GitHub remains authoritative
for story identity, task-board lifecycle, approval, and human-visible status.
The database may cache GitHub references and observed status snapshots needed to
avoid duplicate work, but it never becomes a second task board.

All schema changes use numbered, forward-only, transactional migrations run at
startup.  A destructive rewrite requires a pre-migration backup and explicit
user approval.  Evidence payloads are immutable files in a service-owned
artifact directory addressed by SHA-256 digest; SQLite contains manifests,
references, and redacted audit data rather than large raw payloads.

## SQLite schema

Use SQLite foreign keys, WAL mode, UTC RFC 3339 timestamps, UUIDv7 identifiers,
and JSON text only for versioned structured values whose fields evolve faster
than the relational boundary.  Every table has `created_at`; mutable operational
tables also have `updated_at`.

| Table | Purpose and key fields | Does not own |
| --- | --- | --- |
| `schema_migration` | `version` primary key, checksum, applied timestamp, tool version | Application/task state |
| `poll_checkpoint` | Project/repository reference, last successful scan, cursor observation, lease owner/expiry, API rate metadata | Project Status or approval |
| `dispatch` | Dispatch UUID, GitHub issue/project-item IDs, `ready_generation`, idempotency key, local attempt state, selected model reference, observed external timestamps | Issue title/body, labels, assignees, or lifecycle status |
| `invocation_trace` | Invocation/dispatch IDs, agent role, model fingerprint, redacted request/response digests, timing, token counts when supplied, terminal error class | Raw prompts, credentials, full model output |
| `model_assessment` | Suite version, runtime/model fingerprint, role, configuration digest, metrics, pass/fail, artifact reference | Current routing decision by itself |
| `model_outcome` | Dispatch/invocation IDs, model fingerprint, role, outcome, latency, tool/schema failures, revision count | GitHub story state |
| `artifact_manifest` | SHA-256 digest, logical type, byte size, storage path, producing invocation, retention class, quarantine timestamp | Artifact bytes |
| `evidence_ledger` | Append-only event ID, dispatch/invocation IDs, action type, input/output digests, outcome/error class, artifact reference | Raw tool arguments/results or secrets |
| `cleanup_run` | Run ID, policy version, candidate/deleted/quarantined counts, failures, summary artifact reference | Retention policy history |

`dispatch.local_state` is limited to operational progression such as
`claimed_pending`, `claimed_confirmed`, `running`, `terminal`, or `superseded`.
It is never presented as a substitute for the current GitHub Project Status.
The GitHub adapter reconciles it with current GitHub state before every side
effect.

## Artifact and evidence rules

The artifact store is a service-owned directory outside story worktrees.  An
artifact is written atomically to a temporary file, hashed, fsynced, then moved
to its digest-derived path.  The manifest is inserted only after that move;
retries with the same digest are idempotent.  Typical logical types are
redacted invocation trace, tool-evidence payload, assessment result, test
transcript, review report, and cleanup summary.

The **evidence ledger** is append-only.  One row records each meaningful
agent/tool transition with dispatch and invocation identity, action type,
input/output digests, result or error class, timestamp, and artifact-manifest
reference.  It stores no raw prompt, token, credential, or duplicated payload.
If a full payload is needed for audit, it is redacted before becoming an
artifact and is linked by digest.

## Migration protocol

Migrations are named `NNNN_<purpose>` and carry an immutable checksum.  Startup
holds the local maintenance lease, verifies the applied migration chain, and
runs all pending additive/compatible migrations in a transaction.  If checksum
validation, migration, or integrity checks fail, the service remains unhealthy
and does not poll or dispatch.

For a destructive migration:

1. create and verify a backup of both SQLite and artifact-manifest metadata;
2. require an explicit user approval recorded in the control issue;
3. stop dispatching, run the migration, and run foreign-key/integrity checks;
4. retain the pre-migration backup under the future backup policy; and
5. write an evidence-ledger migration event before resuming.

## Retention and automatic cleanup

| Retention class | Duration |
| --- | --- |
| Ordinary invocation traces, ledger rows, and artifacts | 90 days |
| Evidence linked to a PR or Blocked story | 180 days |
| Migration history and aggregated model-assessment/outcome metrics | Indefinite |

A daily cleanup job selects only expired, unreferenced routine evidence.  It
first moves eligible artifact files to a local quarantine, marks their manifests
with the quarantine timestamp, and retains them for seven days.  After that
window it removes the file and its eligible manifest/ledger rows transactionally
where possible, preserving referential integrity.  Each run appends a
`cleanup_run` row and a redacted cleanup-summary artifact.  Failures leave the
item for a later run; cleanup never deletes a referenced, PR-linked, Blocked,
migration, or aggregate record.

## Acceptance criteria

- Restart at any point in a GitHub claim or specialist invocation cannot create
  a duplicate external side effect because the local dispatch idempotency record
  is reconciled with GitHub first.
- `PRAGMA foreign_key_check` and `PRAGMA integrity_check` pass after every
  migration and cleanup run.
- Changing an artifact byte changes its digest; an artifact cannot be overwritten
  in place.
- Inspecting SQLite or a routine artifact never reveals a credential or raw
  prompt/tool payload.
- The cleanup job is automatic, leaves a seven-day recovery window, and records
  its actions without retaining expired routine evidence indefinitely.
