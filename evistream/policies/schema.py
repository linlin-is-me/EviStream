"""Strict and duplicate-key-safe YAML policy schema."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from evistream.domain import Severity
from evistream.governance.types import AggregationConfig
from evistream.policies.catalog import EXCEPTION_CATALOG

MAX_POLICY_BYTES = 256 * 1024
POLICY_ID_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$"
REQUIREMENT_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
ESCALATION_SENTINELS = {"unresolved_exception", "contradictory_evidence"}


class PolicyError(RuntimeError):
    code = "POLICY_INVALID"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BooleanExpression(StrictModel):
    all: list[str | BooleanExpression] | None = None
    any: list[str | BooleanExpression] | None = None

    @model_validator(mode="after")
    def exactly_one_operator(self) -> BooleanExpression:
        populated = [value for value in (self.all, self.any) if value]
        if len(populated) != 1:
            raise ValueError("boolean expression requires exactly one non-empty operator")
        return self

    def references(self) -> set[str]:
        children = self.all or self.any or []
        result: set[str] = set()
        for child in children:
            if isinstance(child, str):
                result.add(child)
            else:
                result.update(child.references())
        return result


class RequirementSpec(StrictModel):
    id: str = Field(pattern=REQUIREMENT_ID_PATTERN)
    type: Literal["visual_presence", "temporal_context", "speech_content", "text_presence"]
    required: bool = True
    description: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def reserved_prefix(self) -> RequirementSpec:
        if self.id.startswith("exception"):
            raise ValueError("ordinary requirement IDs cannot use the exception prefix")
        return self


class DecisionSpec(StrictModel):
    reject_when: BooleanExpression
    escalate_when: BooleanExpression


class PolicyDocument(StrictModel):
    id: str = Field(pattern=POLICY_ID_PATTERN)
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    severity: Severity
    trigger_terms: list[str] = Field(min_length=1)
    requirements: list[RequirementSpec] = Field(min_length=1)
    exceptions: list[str] = Field(default_factory=list)
    aggregation: AggregationConfig = Field(default_factory=AggregationConfig)
    decision: DecisionSpec

    @model_validator(mode="after")
    def validate_references(self) -> PolicyDocument:
        requirement_ids = [item.id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement IDs must be unique")
        if len(self.trigger_terms) != len(set(self.trigger_terms)):
            raise ValueError("trigger terms must be unique")
        if len(self.exceptions) != len(set(self.exceptions)):
            raise ValueError("exception IDs must be unique")
        unknown_exceptions = set(self.exceptions) - EXCEPTION_CATALOG.keys()
        if unknown_exceptions:
            raise ValueError(f"unknown exceptions: {sorted(unknown_exceptions)}")
        reject_unknown = self.decision.reject_when.references() - set(requirement_ids)
        if reject_unknown:
            raise ValueError(f"reject_when contains unknown references: {sorted(reject_unknown)}")
        allowed_escalation = (
            set(requirement_ids)
            | {f"exception.{item}" for item in self.exceptions}
            | ESCALATION_SENTINELS
        )
        escalation_unknown = self.decision.escalate_when.references() - allowed_escalation
        if escalation_unknown:
            raise ValueError(
                f"escalate_when contains unknown references: {sorted(escalation_unknown)}"
            )
        return self


class LoadedPolicy(StrictModel):
    document: PolicyDocument
    source_yaml: str
    source_sha256: str


class DuplicateKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.Loader, node: yaml.MappingNode, deep: bool = False) -> Any:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key: {key}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


DuplicateKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def load_policy(path: Path) -> LoadedPolicy:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise PolicyError(f"cannot read policy: {path}") from error
    if len(raw) > MAX_POLICY_BYTES:
        raise PolicyError("policy exceeds the 256 KiB limit")
    try:
        source = raw.decode("utf-8")
        return load_policy_source(source)
    except (UnicodeDecodeError, yaml.YAMLError, ValidationError, ValueError) as error:
        raise PolicyError(str(error)) from error


def load_policy_source(source: str) -> LoadedPolicy:
    raw = source.encode("utf-8")
    if len(raw) > MAX_POLICY_BYTES:
        raise PolicyError("policy exceeds the 256 KiB limit")
    try:
        payload = yaml.load(source, Loader=DuplicateKeySafeLoader)
        if not isinstance(payload, dict):
            raise ValueError("policy root must be a mapping")
        document = PolicyDocument.model_validate(payload)
    except (yaml.YAMLError, ValidationError, ValueError) as error:
        raise PolicyError(str(error)) from error
    return LoadedPolicy(
        document=document,
        source_yaml=source,
        source_sha256=sha256(raw).hexdigest(),
    )
