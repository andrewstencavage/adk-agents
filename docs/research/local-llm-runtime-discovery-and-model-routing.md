# Local LLM runtime discovery and model routing

## Decision

Support exactly two configured local model runtimes in the first build:

1. **Ollama**, using its native API at a configured base URL (normally
   `http://127.0.0.1:11434`).
2. **LM Studio**, using its native v1 REST API at a configured base URL
   (normally `http://127.0.0.1:1234`).

The service must not guess by scanning ports, inspect user application files, or
download/load models during normal discovery.  Configuration declares the two
runtime endpoints, enabled state, and a credential reference when needed.  The
runtime adapters then health-check only those endpoints, enumerate their local
inventories, and return a normalized `DiscoveredModel` record.

Use native APIs for discovery and metadata, and use ADK's `LiteLlm` connector
for agent invocation.  For Ollama, configure `ollama_chat/<model>` and set
`OLLAMA_API_BASE`; ADK explicitly recommends `ollama_chat` rather than
`ollama` for tool-using agents.  For LM Studio, use its OpenAI-compatible
endpoint behind a local, runtime-specific `LiteLlm` configuration.  The adapter
owns endpoint details so the specialist contract never sees a raw URL.

No model name is selected in this decision.  A model becomes a routing candidate
only after the capability assessment below has recorded a passing result for its
exact runtime, model identifier, model fingerprint, and runtime version.  Thus
the two runtime choices are stable while the locally installed model inventory
can change safely.

## Target-host boundary

The service is being installed on a different machine from this planning
workspace.  That target host is the only place where runtime discovery and
capability assessment run.  Its deployment configuration supplies the two
loopback-or-private-network endpoint references and stores any API token in the
target host's secret mechanism; neither the planner nor the GitHub task board
discovers or contains a target-host credential.  Consequently, the exact model
inventory is an installation-time fact to capture through the target-host
provisioning task, rather than a fact inferred here.

## Discovery contract

```text
Runtime configuration -> runtime adapter -> native inventory/metadata
                                            -> DiscoveredModel[]
                                            -> assessment queue

Manager dispatch -> route request -> eligible assessed models -> deterministic rank
                                                             -> selected ModelRef
```

`DiscoveredModel` has:

- `runtime_id` (`ollama` or `lm_studio`), `base_url_ref`, and runtime version;
- provider model ID plus display name; and
- immutable/revalidation fields: Ollama digest, or LM Studio key plus its
  architecture, quantization, size, and configured maximum context; and
- advertised traits: model type, context length where known, tool-use flag,
  structured-output support, loaded state, and discovery timestamp.

Ollama discovery calls `GET /api/tags` and then `POST /api/show` for each
candidate.  It records the digest and capabilities (in particular `tools`).
LM Studio discovery calls `GET /api/v1/models`; it records only `type: llm`
models and their key, architecture, quantization, and size.  When a capability
is not supplied by that endpoint, the assessment—not an assumed model family—
establishes it.  A runtime that is unavailable, unauthorized, or returns no
eligible model is `unavailable`; it is not silently replaced by a cloud model.

The polling loop refreshes inventory on startup, on a configurable interval
(default 15 minutes), and before dispatch when a cached inventory is older than
five minutes.  A fingerprint change invalidates every previous assessment for
that `ModelRef` and returns it to `discovered` status.

## Capability assessment

Assess every discovered LLM in a separate, bounded worker.  It has no GitHub,
filesystem, credential, or general network tools.  The suite is versioned and
is run with a fixed prompt pack, low deterministic sampling settings where the
runtime supports them, one warm-up, and three scored repetitions.  Store every
raw response only as a redacted artifact; store parsed metrics and error classes
in the local system record.

### Universal admission gates

A candidate must pass all of these before it is eligible for any specialist:

1. Reachability and bounded-response test.
2. `SpecialistResult` JSON-schema generation and Pydantic validation.
3. A required, single typed tool call with exact tool name and valid arguments,
   followed by a correct final response using the supplied tool result.
4. Refusal of a prompt that asks it to invent a tool result or circumvent a
   declared scope.
5. Three consecutive runs without malformed output, unexpected extra tool call,
   or timeout.

Tool use is tested through the same ADK/LiteLLM invocation path intended for
the specialist.  Advertised runtime capability is a discovery hint, never an
admission result.  This matters because LM Studio notes that small or
non-tool-trained models can produce unparseable calls, and Ollama requires a
model with tool support for tool-using agents.

### Role suites and thresholds

| Role | Additional scored work | Eligible when |
| --- | --- | --- |
| Manager | Classify a control issue, choose one named specialist, emit a schema-valid dispatch with no ungranted action | universal gates + >=90% exact dispatch/correct abstention |
| Scrum Master | Apply supplied task-board transition rules and produce an idempotent typed update proposal | universal gates + >=95% valid proposal/rule adherence |
| Research | Build a bounded research plan, distinguish evidence from inference, and return cited claims from supplied search results | universal gates + >=85% supported-claim score and zero invented citations |
| Coding | Make a small repository change from a provided fixture, request only allowed commands, interpret test output, and return a bounded patch plan | universal gates + >=80% fixture tests passing and zero scope-policy violations |
| Review | Find seeded defects and security/dependency issues in a fixture, produce actionable findings, and correctly accept a clean fixture | universal gates + >=85% weighted defect recall, >=90% precision, and zero false accept on critical seeds |

The fixture pack, threshold set, and scoring implementation are versioned in
the eventual evaluation artifact.  A failed role suite does not globally reject
a model: it can still be eligible for another role whose gates it passes.

## Assessment and outcome records

Create immutable `ModelAssessment` rows keyed by:

`assessment_id, suite_version, runtime_id, model_id, fingerprint, runtime_version,
role, started_at, completed_at, configuration_digest, status, metrics_json,
artifact_ref`.

The routing view selects the most recent passing assessment for the exact
fingerprint and current suite version.  Each real specialist invocation appends
a `ModelOutcome` keyed by dispatch and invocation ID with selected model,
role, user override indicator, terminal result, latency, prompt/completion
tokens when supplied, tool/schema failures, revision count, and a redacted
evidence reference.  Outcomes are operational evidence, not a replacement for
GitHub story state.

On a scheduled weekly assessment—or after five terminal failures for the same
fingerprint/role—the service reruns the relevant suite.  A runtime error is
recorded separately from a model quality failure.  A model with stale or failed
assessment is ineligible for tool-using specialist work.

## Routing rule and user override

For each dispatch, the Manager sends a typed `RouteRequest` containing the
specialist role, estimated context/input size, required capabilities, priority,
and optional user override model reference.  It then:

1. Filters to enabled, reachable models with a current passing assessment for
   that role and all required capabilities.
2. Rejects an override outside that eligible set with a visible explanation;
   it never turns an unassessed model into an authority-bearing agent.
3. Uses an eligible override exactly as requested.  Otherwise ranks candidates
   deterministically by role-suite score (60%), recent same-role success rate
   (25%), and normalized warm latency (15%).  A candidate needs at least ten
   recent outcomes before its outcome score can outweigh its assessment score.
4. Records the complete ranking inputs, selected `ModelRef`, and reason in the
   dispatch trace.

The first build has **no silent quality fallback**.  If the selected runtime
fails before an output is emitted and the user did not pin a model, the Manager
may retry once with the next eligible candidate and records both attempts.  A
model/schema/tool failure after output is a normal specialist failure or review
loop event, not an excuse to switch models mid-task.  If no eligible candidate
exists, the Scrum Master creates a blocked story explaining the missing
capability and asks the user to install, expose, or assess a model.

## Build acceptance criteria

- With configured Ollama and LM Studio endpoints, discovery produces normalized
  records without any port scan, model download, model load, or secret in logs.
- Changing an Ollama digest or LM Studio fingerprint invalidates the old
  assessment before the model can receive a new dispatch.
- A model that merely advertises tool support cannot route a task until it
  passes the real ADK-path tool and schema gates.
- Identical current assessments and outcomes produce the same unoverridden
  selection, and the trace explains that choice.
- An eligible user override is honored; an ineligible override is rejected and
  visible in the control issue rather than silently falling back.
- A missing eligible model blocks the specialist story; it does not create a
  cloud call or broaden model permissions.

## Sources

- [ADK Ollama model host](https://adk.dev/agents/models/ollama/) — LiteLLM
  integration, required `ollama_chat` provider form, endpoint configuration,
  and tool-support caveat.
- [Ollama local-model inventory](https://docs.ollama.com/api/tags) and
  [chat API](https://docs.ollama.com/api/chat) — native model metadata, tools,
  and JSON-schema response format.
- [LM Studio native REST API](https://lmstudio.ai/docs/developer/rest) and
  [model inventory](https://lmstudio.ai/docs/developer/rest/list) — native v1
  APIs and model metadata.
- [LM Studio tool-use guidance](https://beta.lmstudio.ai/docs/developer/openai-compat/tools)
  — tool-call reliability depends on the model, not merely the endpoint.
- [ADK evaluation guidance](https://adk.dev/evaluate/) — versioned evaluation
  cases and response/trajectory/tool-use criteria.
