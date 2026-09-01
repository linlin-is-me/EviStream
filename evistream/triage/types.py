"""Stable automatic triage contracts."""

from enum import StrEnum

from pydantic import BaseModel, Field


class TriageAction(StrEnum):
    SKIP = "SKIP"
    CREATE_CASE = "CREATE_CASE"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class TriageOutput(BaseModel):
    action: TriageAction
    confidence: float = Field(ge=0, le=1)
    reason_code: str = Field(min_length=1, max_length=64)
    matched_terms: list[str] = Field(default_factory=list)
    matched_requirement_keys: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=2000)
