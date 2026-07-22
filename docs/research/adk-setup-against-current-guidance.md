# ADK setup against current guidance

## Scope

This compares the repository's planned Python ADK application and the target
host's July 18 runtime inventory with the current ADK reference supplied by the
user: <https://adk.dev/llms-full.txt>.  The repository contains planning and
provisioning material; it does not yet contain a Python package, an ADK agent,
or application tests.

## Alignment

- The proposed Manager plus bounded specialists matches ADK's support for
  multi-agent systems. Keeping tool grants narrow and using deterministic
  workflow steps where appropriate is a strong fit for the documented
  `LlmAgent` and workflow-agent model.
- Persisted sessions, artifacts, callbacks, and a Runner are all first-class
  ADK concepts. The planned SQLite session history, file-backed evidence, and
  callback-based policy/evidence boundaries therefore use supported extension
  points.
- Calling Ollama through ADK's LiteLLM connector using the `ollama_chat/`
  provider form is consistent with the project's runtime-routing decision.
- Versioned task/model evaluations are consistent with ADK's built-in evaluation
  capability, including multi-turn datasets and trajectory/tool-use criteria.

## Gaps and risks before implementation

1. **No executable ADK boundary exists.** There is no `pyproject.toml`, lockfile,
   agent package, service entry point, or automated test/evaluation harness.
   The documented 2.5.x choice is a decision, not an installed dependency.
2. **The plan relies on concrete 2.5.x APIs without a compatibility spike.**
   Before building policy around tasks, SQLite sessions, artifacts, and callback
   ordering, create one minimal pinned-environment integration test for those
   exact interfaces. This is especially important because the supplied guide
   describes ADK 2.0 as a graph-runtime boundary with changed event/session
   behavior.
3. **Model availability is not sufficient for routing.** The current inventory
   has three Ollama chat models which advertise `tools`; LM Studio has only an
   unloaded embedding model. None has a recorded ADK-path structured-output and
   tool-call assessment, so no model is ready to receive an authority-bearing
   specialist task under the repository's own policy.
4. **Production concerns are only specified, not verified.** The docs caution
   that ADK Web is development-only. The repository needs a service launcher,
   configuration/secret contract, graceful restart behavior, and deployment
   health/observability checks rather than treating the developer UI as the
   runtime.

## Recommended order

1. Create the pinned Python project and lockfile, plus a small ADK 2.5.x spike
   that proves task dispatch, session persistence, artifact read/write, and a
   denied tool callback.
2. Turn that spike into CI tests, then implement only the Manager-to-one-
   specialist vertical slice with the typed result contract.
3. Run the existing capability assessment suite through the real LiteLLM/ADK
   path; promote only passing Ollama models. Add a real LM Studio chat model if
   redundancy across runtimes is required.
4. Implement the SQLite evidence/retention and GitHub adapters behind their
   already documented narrow interfaces, then add deployment/runbook checks.

## Sources

- [Current ADK documentation](https://adk.dev/llms-full.txt): agents, workflow
  agents, tools, callbacks, sessions, artifacts, Runner, evaluation, models,
  local development, and ADK 2.0 migration guidance.
- [Repository application boundary](adk-application-boundary-and-specialist-contracts.md)
  and [model-routing decision](local-llm-runtime-discovery-and-model-routing.md).
- Target-host inventory: `var/provisioning/initial-runtime-inventory.json`
  (generated and intentionally ignored by Git).
