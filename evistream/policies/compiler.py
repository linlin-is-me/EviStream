"""Deterministic conversion from validated policy YAML to executable data."""

import json
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evistream.domain import Severity
from evistream.policies.catalog import EXCEPTION_CATALOG, REQUIREMENT_CAPABILITIES
from evistream.policies.schema import BooleanExpression, LoadedPolicy, PolicyDocument

COMPILER_VERSION = "1"


class CompiledModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRequirementTemplate(CompiledModel):
    requirement_key: str
    requirement_type: str
    source_kind: Literal["requirement", "exception"]
    required: bool
    description: str
    suggested_queries: list[str]
    modalities: list[str]
    tool_capabilities: list[str]
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CompiledPolicy(CompiledModel):
    policy_id: str
    version: int
    name: str
    enabled: bool
    severity: Severity
    trigger_terms: list[str]
    requirements: list[EvidenceRequirementTemplate]
    reject_when: BooleanExpression
    escalate_when: BooleanExpression
    compiler_version: str
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PolicyCompiler:
    def compile(self, source: LoadedPolicy | PolicyDocument) -> CompiledPolicy:
        document = source.document if isinstance(source, LoadedPolicy) else source
        templates = [self._ordinary(item, document.trigger_terms) for item in document.requirements]
        templates.extend(self._exception(item) for item in document.exceptions)
        base = {
            "policy_id": document.id,
            "version": document.version,
            "name": document.name,
            "enabled": document.enabled,
            "severity": document.severity,
            "trigger_terms": document.trigger_terms,
            "requirements": templates,
            "reject_when": document.decision.reject_when,
            "escalate_when": document.decision.escalate_when,
            "compiler_version": COMPILER_VERSION,
        }
        return CompiledPolicy(**base, semantic_sha256=_hash(base))

    @staticmethod
    def _ordinary(spec: object, trigger_terms: list[str]) -> EvidenceRequirementTemplate:
        from evistream.policies.schema import RequirementSpec

        if not isinstance(spec, RequirementSpec):
            raise TypeError("invalid requirement spec")
        capability = REQUIREMENT_CAPABILITIES[spec.type]
        payload = {
            "requirement_key": spec.id,
            "requirement_type": spec.type,
            "source_kind": "requirement",
            "required": spec.required,
            "description": spec.description,
            "suggested_queries": _unique([spec.description, *trigger_terms]),
            "modalities": list(capability.modalities),
            "tool_capabilities": list(capability.tools),
        }
        return EvidenceRequirementTemplate(**payload, semantic_sha256=_hash(payload))

    @staticmethod
    def _exception(exception_id: str) -> EvidenceRequirementTemplate:
        definition = EXCEPTION_CATALOG[exception_id]
        capability = REQUIREMENT_CAPABILITIES[definition.requirement_type]
        payload = {
            "requirement_key": f"exception.{exception_id}",
            "requirement_type": definition.requirement_type,
            "source_kind": "exception",
            "required": True,
            "description": definition.description,
            "suggested_queries": list(definition.query_terms),
            "modalities": list(capability.modalities),
            "tool_capabilities": list(capability.tools),
        }
        return EvidenceRequirementTemplate(**payload, semantic_sha256=_hash(payload))


def _hash(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda item: item.model_dump(mode="json")
        if isinstance(item, BaseModel)
        else str(item),
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))
