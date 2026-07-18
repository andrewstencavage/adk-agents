# Target-host model candidate screen

## Result

There is **no initially eligible specialist-routing model**. Eligibility is
deliberately empty until a target-host candidate passes the versioned universal
and role-specific suites through the intended ADK/LiteLLM path.

The target host currently has three generative Ollama candidates and one LM
Studio embedding model:

| Installed model | Screening result | Assessment status |
| --- | --- | --- |
| `llama3.1:8b` | Candidate for all specialist suites. Ollama advertises tool support and positions the 8B family for coding assistants. | Awaiting exact host digest and live tests. |
| `deepseek-r1:7b` | Candidate for all specialist suites. This is the 7B DeepSeek-R1-Distill-Qwen variant, not the full R1 release. | Awaiting exact host digest and live tests. |
| `deepseek-64k:latest` | Unknown. This unnamespaced local tag cannot be mapped safely to an upstream family or capability claim. | Capture native metadata before any test or route. |
| `text-embedding-nomic-embed-text-v1.5` | Not a specialist candidate: it is an embedding/feature-extraction model, suitable only for future retrieval/indexing. | Excluded from Manager, Scrum Master, Research, Coding, and Review suites. |

## Evidence and constraints

Ollama's `llama3.1` library page advertises tool use and identifies the 8B
model as a coding-assistant-capable general model. Its `deepseek-r1` page lists
the 7B distillation with tool support, but advertised capability is only a
screening signal—not evidence that the installed quantization will obey this
project's typed tools or schema constraints.

Ollama supports both tool-call schemas and JSON/JSON-Schema structured output
through its local API. The required evidence is nevertheless the actual target
model output on the same provider path that ADK will use. `GET /api/tags` and
`POST /api/show` supply the name, digest, family, parameter size,
quantization, template, and advertised capabilities needed to bind each result
to an exact `ModelRef`.

Nomic's model card describes `nomic-embed-text-v1.5` as a 0.1B embedding
model. It produces vectors (with task prefixes and configurable embedding
dimensions), not assistant responses or typed tool calls.

## Required target-host assessment

Before any specialist is routable:

1. Capture and retain the redacted `GET /api/tags` and `POST /api/show` output
   for every installed Ollama candidate, especially `deepseek-64k:latest`.
2. Exercise each generative candidate using the eventual ADK/LiteLLM
   configuration with one warm-up and three scored repetitions at low,
   deterministic sampling.
3. Require the universal gates: bounded response, `SpecialistResult`
   JSON-schema/Pydantic validation, exactly one valid typed tool call followed
   by a correct final answer, scope-circumvention refusal, and three clean
   repetitions.
4. Score the role suites and thresholds already defined in
   [Local LLM runtime discovery and model routing](local-llm-runtime-discovery-and-model-routing.md).
   Record the exact digest, runtime version, suite version, configuration
   digest, metrics, failures, and a redacted evidence reference.

Until these runs exist, the routing table must return no eligible model and
block the requested specialist story rather than guess, silently use a cloud
model, or promote an advertised feature to an authorization.

## Sources

- [Ollama Llama 3.1 library](https://ollama.com/library/llama3.1) — model
  family, tool-use advertisement, and coding-assistant positioning.
- [Ollama DeepSeek-R1 library](https://ollama.com/library/deepseek-r1) — 7B
  distillation, context, and tool-use advertisement.
- [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling) and
  [structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
  — local API tool and schema mechanisms.
- [Ollama model inventory API](https://docs.ollama.com/api/tags) — exact local
  model metadata and model-detail discovery.
- [Nomic Embed Text v1.5 model card](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5)
  — embedding-only purpose and vector behavior.
