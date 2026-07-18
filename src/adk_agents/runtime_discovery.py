"""Configured local-runtime inventory adapters; they never probe arbitrary ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .routing import ModelRef


class HttpTransport(Protocol):
    def request(self, method: str, url: str, body: object | None = None) -> object: ...


@dataclass(frozen=True)
class RuntimeConfig:
    runtime_id: str
    base_url: str
    runtime_version: str
    enabled: bool = True
    credential_ref: str | None = None

    def __post_init__(self) -> None:
        if self.runtime_id not in {"ollama", "lm_studio"}:
            raise ValueError("only configured Ollama and LM Studio runtimes are supported")
        if not self.base_url.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError("runtime endpoint must be a configured loopback endpoint")


@dataclass(frozen=True)
class DiscoveredModel:
    ref: ModelRef
    display_name: str
    model_type: str
    capabilities: frozenset[str]


class RuntimeDiscovery:
    """Discovers only each enabled configured endpoint's native inventory."""

    def __init__(self, configurations: list[RuntimeConfig], transport: HttpTransport) -> None:
        self._configurations = configurations
        self._transport = transport

    def discover(self) -> list[DiscoveredModel]:
        models: list[DiscoveredModel] = []
        for config in self._configurations:
            if config.enabled:
                models.extend(self._ollama(config) if config.runtime_id == "ollama" else self._lm_studio(config))
        return models

    def _ollama(self, config: RuntimeConfig) -> list[DiscoveredModel]:
        response = self._transport.request("GET", f"{config.base_url}/api/tags")
        result: list[DiscoveredModel] = []
        for item in _mapping(response).get("models", []):
            model = _mapping(item)
            model_id, digest = str(model["name"]), str(model["digest"])
            details = _mapping(self._transport.request("POST", f"{config.base_url}/api/show", {"name": model_id}))
            result.append(DiscoveredModel(ModelRef("ollama", model_id, digest, config.runtime_version), model_id, "llm", frozenset(map(str, details.get("capabilities", [])))))
        return result

    def _lm_studio(self, config: RuntimeConfig) -> list[DiscoveredModel]:
        response = _mapping(self._transport.request("GET", f"{config.base_url}/api/v1/models"))
        result: list[DiscoveredModel] = []
        for item in response.get("data", []):
            model = _mapping(item)
            if model.get("type") != "llm":
                continue
            model_id = str(model["key"])
            fingerprint = "|".join(str(model.get(key, "unknown")) for key in ("key", "architecture", "quantization", "size_bytes", "max_context_length"))
            result.append(DiscoveredModel(ModelRef("lm_studio", model_id, fingerprint, config.runtime_version), model_id, "llm", frozenset()))
        return result


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("runtime returned an invalid inventory response")
    return value
