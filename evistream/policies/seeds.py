"""Validation and optional materialization of Stage 2 demo case metadata."""

from collections import Counter
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from evistream.domain import PolicyLifecycle, RequirementStatus, Verdict
from evistream.media.types import VideoStatus
from evistream.policies.compiler import PolicyCompiler
from evistream.policies.schema import PolicyError, load_policy
from evistream.policies.versioning import create_case, save_policy
from evistream.storage.database import Database
from evistream.storage.models import VideoRecord


class SeedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseSeed(SeedModel):
    case_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    scenario: Literal["clear_violation", "context_exception", "insufficient_evidence"]
    fixture_ref: str = Field(min_length=1)
    policy_id: str
    policy_version: int = Field(ge=1)
    expected_verdict: Verdict
    expected_requirement_results: dict[str, RequirementStatus]
    reason: str = Field(min_length=1)
    annotation_status: Literal["metadata_only", "human_verified"] = "metadata_only"


class CaseSeedManifest(SeedModel):
    cases: list[CaseSeed]


class VideoMap(SeedModel):
    videos: dict[str, str]


class SeedSummary(SeedModel):
    policy_count: int
    case_count: int
    scenarios: dict[str, int]
    materialized_case_ids: list[str] = Field(default_factory=list)


def load_case_seeds(path: Path) -> CaseSeedManifest:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        return CaseSeedManifest.model_validate(payload)
    except (OSError, yaml.YAMLError, ValueError) as error:
        raise PolicyError(f"invalid case seed manifest: {error}") from error


def validate_demo_seeds(policy_dir: Path, manifest_path: Path) -> SeedSummary:
    compiler = PolicyCompiler()
    compiled = {
        policy.policy_id: policy
        for policy in (
            compiler.compile(load_policy(path)) for path in sorted(policy_dir.glob("*.yaml"))
        )
    }
    manifest = load_case_seeds(manifest_path)
    if len(compiled) != 3 or len(manifest.cases) != 9:
        raise PolicyError("Stage 2 requires exactly three policies and nine case seeds")
    keys = [item.case_key for item in manifest.cases]
    if len(keys) != len(set(keys)):
        raise PolicyError("case seed keys must be unique")
    scenarios = Counter(item.scenario for item in manifest.cases)
    if scenarios != {
        "clear_violation": 3,
        "context_exception": 3,
        "insufficient_evidence": 3,
    }:
        raise PolicyError("each Stage 2 scenario must appear exactly three times")
    policy_counts = Counter(item.policy_id for item in manifest.cases)
    if any(policy_counts[policy_id] != 3 for policy_id in compiled):
        raise PolicyError("each policy must have exactly three case seeds")
    for item in manifest.cases:
        policy = compiled.get(item.policy_id)
        if policy is None or policy.version != item.policy_version:
            raise PolicyError(f"case seed references an unknown policy: {item.case_key}")
        known_requirements = {requirement.requirement_key for requirement in policy.requirements}
        unknown = item.expected_requirement_results.keys() - known_requirements
        if unknown:
            raise PolicyError(f"case seed has unknown requirements: {sorted(unknown)}")
        if item.scenario == "clear_violation" and item.expected_verdict is not Verdict.REJECT:
            raise PolicyError("clear violations must expect REJECT")
        if item.scenario == "context_exception" and item.expected_verdict is not Verdict.APPROVE:
            raise PolicyError("context exceptions must expect APPROVE")
        if (
            item.scenario == "insufficient_evidence"
            and item.expected_verdict is not Verdict.NEEDS_HUMAN_REVIEW
        ):
            raise PolicyError("insufficient evidence must expect human review")
    return SeedSummary(
        policy_count=len(compiled),
        case_count=len(manifest.cases),
        scenarios=dict(scenarios),
    )


def apply_demo_seeds(
    database: Database,
    policy_dir: Path,
    manifest_path: Path,
    video_map_path: Path,
    *,
    model_profile: str,
) -> SeedSummary:
    summary = validate_demo_seeds(policy_dir, manifest_path)
    manifest = load_case_seeds(manifest_path)
    try:
        video_map = VideoMap.model_validate(
            yaml.safe_load(video_map_path.read_text(encoding="utf-8"))
        )
    except (OSError, yaml.YAMLError, ValueError) as error:
        raise PolicyError(f"invalid video map: {error}") from error
    missing = sorted({item.fixture_ref for item in manifest.cases} - video_map.videos.keys())
    if missing:
        raise PolicyError(f"video map is missing fixture references: {missing}")
    sources = [load_policy(path) for path in sorted(policy_dir.glob("*.yaml"))]
    compiler = PolicyCompiler()
    materialized: list[str] = []
    with database.session() as session:
        video_ids = set(video_map.videos.values())
        videos = session.scalars(select(VideoRecord).where(VideoRecord.id.in_(video_ids))).all()
        ready = {video.id for video in videos if video.status == VideoStatus.READY}
        if ready != video_ids:
            raise PolicyError(
                f"mapped videos are missing or not ready: {sorted(video_ids - ready)}"
            )
        for source in sources:
            save_policy(
                session,
                source,
                compiler.compile(source),
                PolicyLifecycle.PUBLISHED,
            )
        for item in manifest.cases:
            bundle = create_case(
                session,
                video_map.videos[item.fixture_ref],
                item.policy_id,
                item.policy_version,
                model_profile,
                case_id=item.case_key,
            )
            materialized.append(bundle.case.case_id)
    return summary.model_copy(update={"materialized_case_ids": materialized})
