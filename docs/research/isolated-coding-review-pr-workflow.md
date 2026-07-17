# Isolated coding, review, and pull-request workflow

## Decision

Each **specialist story** receives an isolated **story worktree** from protected
`main` on a branch named `agent/<issue>-<slug>`.  Coding is intentionally not a
Git or credential-bearing actor: it edits permitted files and runs the
Python-only command profile.  A constrained host-side **commit authority**
records the verified worktree diff as a commit; Review evaluates the committed
branch from a separate read-only checkout.  Only an accepted review can create
a normal PR, and the user alone approves and merges it.

## Worktree and file boundary

1. The host verifies `main` is clean and up to date, creates a fresh worktree
   for the story branch, and records its base commit.
2. Coding receives only the story worktree path, story acceptance criteria,
   approved paths, and the Python command profile.  It has no Git command,
   GitHub credential, general shell, network access, or package-install
   capability.
3. The default writable paths are source, tests, and documentation explicitly
   relevant to the story.  Dependency manifests and lockfiles, CI/workflow
   files, `.git`, service configuration, and all paths outside the worktree are
   excluded unless the user approves a **scope expansion**.
4. When Coding needs an excluded command or path, it reports a scope gap to the
   Manager.  The Manager asks the user; the Scrum Master records an approved
   expansion on the story before Coding resumes.  There is no self-authorized
   exception.

## Python-only command profile

The initial profile is selected from a repository manifest and may invoke only
the repository's configured, non-network Python checks, such as:

- `uv run pytest ...`;
- `uv run ruff check ...` and `uv run ruff format --check ...`; and
- a configured type-check command (for example `uv run mypy ...`).

It cannot run package installation, dependency resolution/download, arbitrary
shell commands, non-Python profiles, or externally networked commands.  Adding
a language profile is a separately reviewed decision with its own command and
review fixtures.

## Commit and publication boundary

After Coding reports completion, the host independently runs the required
profile and checks the allowed-path diff.  If those checks pass, **commit
authority** creates one commit on the story branch.  Coding never invokes Git.
Each accepted correction creates a new commit; commits are not amended away.
Any eventual squash is the user's merge choice.

The host holds one repository-scoped fine-grained **change-publication
credential** with Contents write and Pull requests write.  It may push an
accepted story branch and create a PR, but it is not exposed to Coding and is
never used to approve, merge, or change protected-branch rules.  Protection on
`main` is required as a defense in depth measure.

## Independent review and correction loop

Review receives a separate read-only checkout of the committed story branch.
It may run the same Python command profile but cannot edit source, create a
commit, or access Coding's worktree.  The Review gate blocks acceptance for:

- unmet story acceptance criteria;
- failing approved tests, lint, or type checks;
- an unauthorized scope change; or
- an unapproved security, dependency, or CI/workflow change.

Style suggestions that do not violate an explicit requirement are non-blocking.
Every blocking **review finding** must state severity, file/path evidence, the
violated requirement, and concrete remediation.  A finding is the sole reason
to request another Coding correction.

An apparent transient infrastructure failure—timeout, unavailable local
service, or runner failure—gets one automatic rerun by Review.  A repeated
failure blocks the story rather than consuming a correction cycle.

There are at most two correction cycles after the initial review:

```text
Coding -> verified commit -> Review
                         -> accept -> PR
                         -> actionable findings -> Coding (cycle 1)
                         -> actionable findings -> Coding (cycle 2)
                         -> still rejected -> Blocked story / user escalation
```

The Scrum Master blocks a story on a scope gap awaiting the user, a repeated
transient failure, or an unresolved result after the second correction cycle.

## Story updates, PRs, and retention

Every handoff is a structured, append-only story update containing branch and
commit SHA, changed-file summary, approved command results, review-cycle count,
artifact links, and a concise Blocked reason where relevant.  It excludes raw
prompts, credentials, and unredacted logs.

After Review accepts, it creates a normal, ready-for-review PR from
`agent/<issue>-<slug>` to protected `main`.  The standard body includes the
story link, implementation summary, commit SHA(s), test results, Review-gate
version, and revision count.  The PR does not represent approval: only the user
approves and merges it.

At terminal story state, remove the local worktree immediately.  Let GitHub
automatically delete a merged branch.  Retain a remote branch for an unmerged
closed or Blocked PR for 14 days, then clean it up through the constrained host
workflow.

## Acceptance criteria

- Coding cannot execute Git, access GitHub credentials, alter excluded files,
  install dependencies, or make network calls without a recorded scope
  expansion.
- Commit authority rejects an unauthorized diff or failed required check and
  produces an auditable commit only from verified worktree state.
- Review runs from a read-only checkout and returns either acceptance or
  actionable findings.
- A third unresolved review result, repeated transient failure, or missing
  scope approval produces a visible Blocked story.
- Every accepted story has one normal PR to protected `main`; no agent approves
  or merges it.
- Story updates allow the user to trace each commit, check result, review cycle,
  PR, and escalation without exposing sensitive data.
