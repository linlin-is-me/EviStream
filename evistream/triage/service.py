"""Idempotent per-policy video triage using provider-neutral gateways."""

import base64
import mimetypes
from hashlib import sha256
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from evistream.config import Settings
from evistream.governance.service import GovernanceApplicationService
from evistream.models import ModelRole, build_model_gateway
from evistream.models.profiles import load_model_profile, resolve_model_profile
from evistream.models.types import MediaReference, ModelError, ModelMessage, ModelRequest
from evistream.policies.compiler import CompiledPolicy
from evistream.policies.versioning import CaseApplicationService
from evistream.retrieval.text import normalize_text
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    ArtifactRecord,
    ModelCallRecord,
    PolicyRecord,
    SearchDocumentRecord,
    VideoRecord,
    VideoTriageCheckRecord,
)
from evistream.triage.types import TriageAction, TriageOutput


class TriageError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class VideoTriageService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def triage(self, job_id: str, video_id: str, profile_name: str) -> int:
        if not self.settings.auto_triage:
            self._set_video_status(video_id, "SUCCEEDED")
            return 0
        policies = self._latest_enabled_policies()
        self._set_video_status(video_id, "RUNNING")
        completed = 0
        try:
            for record in policies:
                if await self._check_policy(job_id, video_id, profile_name, record):
                    completed += 1
        except Exception:
            self._set_video_status(video_id, "FAILED")
            raise
        self._set_video_status(video_id, "SUCCEEDED")
        return completed

    def _latest_enabled_policies(self) -> list[PolicyRecord]:
        with self.database.session() as session:
            records = list(
                session.scalars(
                    select(PolicyRecord)
                    .where(
                        PolicyRecord.lifecycle == "PUBLISHED",
                        PolicyRecord.enabled.is_(True),
                    )
                    .order_by(PolicyRecord.policy_id, PolicyRecord.version.desc())
                ).all()
            )
            latest: dict[str, PolicyRecord] = {}
            for record in records:
                latest.setdefault(record.policy_id, record)
            return list(latest.values())

    async def _check_policy(
        self,
        job_id: str,
        video_id: str,
        profile_name: str,
        policy_record: PolicyRecord,
    ) -> bool:
        try:
            policy = CompiledPolicy.model_validate(policy_record.compiled_policy)
        except ValidationError as error:
            raise TriageError(
                "TRIAGE_FAILED",
                f"published policy has an invalid compiled representation: "
                f"{policy_record.policy_id}@{policy_record.version}",
                retryable=False,
            ) from error
        request_key = sha256(
            f"TRIAGE:{video_id}:{policy.policy_id}:{policy.version}:{profile_name}".encode()
        ).hexdigest()
        with self.database.session() as session:
            existing = session.scalar(
                select(VideoTriageCheckRecord).where(
                    VideoTriageCheckRecord.video_id == video_id,
                    VideoTriageCheckRecord.policy_id == policy.policy_id,
                    VideoTriageCheckRecord.policy_version == policy.version,
                )
            )
            if existing is not None and existing.status == "SUCCEEDED":
                return False
            now = utc_now()
            if existing is None:
                existing = VideoTriageCheckRecord(
                    id=f"triage_{uuid4().hex}",
                    job_id=job_id,
                    video_id=video_id,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    model_profile=profile_name,
                    request_key=request_key,
                    status="RUNNING",
                    attempt=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(existing)
            else:
                existing.status = "RUNNING"
                existing.attempt += 1
                existing.error_code = None
                existing.updated_at = now

        text = self._video_text(video_id)
        profile = load_model_profile(self.settings.model_config_dir, profile_name)
        resolved = resolve_model_profile(
            profile, ModelRole.TRIAGE, self.settings.model_environment()
        )
        frames = (
            self._representative_frames(video_id)
            if resolved.capabilities.image and profile.gateway != "mock"
            else ()
        )
        model_call_id: str | None = None
        try:
            if profile.gateway == "mock":
                output = _mock_output(policy, text)
            else:
                gateway = build_model_gateway(
                    self.settings.model_config_dir,
                    profile_name,
                    ModelRole.TRIAGE,
                    environment=self.settings.model_environment(),
                )
                response = await gateway.generate(
                    ModelRequest(
                        role=ModelRole.TRIAGE,
                        messages=[
                            ModelMessage(
                                role="system",
                                content=(
                                    "Classify this video summary for exactly one policy. Return "
                                    "JSON with action, confidence, reason_code, matched_terms, "
                                    "matched_requirement_keys and summary."
                                ),
                            ),
                            ModelMessage(
                                role="user",
                                content=_prompt(policy, text),
                            ),
                        ],
                        media=frames,
                        response_schema=TriageOutput,
                        timeout_seconds=resolved.defaults.timeout_seconds,
                        trace_id=f"triage-{video_id}-{policy.policy_id}",
                    )
                )
                output = TriageOutput.model_validate(response.data)
                model_call_id = f"model_call_{uuid4().hex}"
                model_call_id = self._record_model_call(
                    model_call_id,
                    job_id,
                    video_id,
                    request_key,
                    profile_name,
                    resolved.model,
                    response.actual_model,
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                    response.usage.total_tokens,
                    response.latency_ms,
                    response.provider_request_id,
                    "success",
                    None,
                    policy,
                    bool(frames),
                )
            _validate_output(policy, output)
        except ModelError as error:
            if profile.gateway != "mock":
                self._record_model_call(
                    f"model_call_{uuid4().hex}",
                    job_id,
                    video_id,
                    request_key,
                    profile_name,
                    resolved.model,
                    None,
                    0,
                    0,
                    0,
                    0,
                    None,
                    "failed",
                    str(error.code),
                    policy,
                    bool(frames),
                )
            self._fail_check(video_id, policy, str(error.code))
            raise TriageError(str(error.code), str(error), retryable=error.retryable) from error
        except TriageError as error:
            self._fail_check(video_id, policy, error.code)
            raise

        case_id: str | None = None
        if output.action is not TriageAction.SKIP:
            bundle = CaseApplicationService(self.database).create_case(
                video_id, policy.policy_id, policy.version, profile_name
            )
            case_id = bundle.case.case_id
            if output.action is TriageAction.NEEDS_HUMAN_REVIEW:
                GovernanceApplicationService(self.database).finalize_case(
                    case_id, allow_without_investigation=True
                )
        with self.database.session() as session:
            check = session.scalar(
                select(VideoTriageCheckRecord).where(
                    VideoTriageCheckRecord.video_id == video_id,
                    VideoTriageCheckRecord.policy_id == policy.policy_id,
                    VideoTriageCheckRecord.policy_version == policy.version,
                )
            )
            if check is None:
                raise RuntimeError("triage checkpoint disappeared")
            check.status = "SUCCEEDED"
            check.action = output.action
            check.confidence = output.confidence
            check.reason_code = output.reason_code
            check.matched_terms = output.matched_terms
            check.matched_requirement_keys = output.matched_requirement_keys
            check.summary = output.summary
            check.case_id = case_id
            check.model_call_id = model_call_id
            check.updated_at = utc_now()
        return True

    def _video_text(self, video_id: str) -> str:
        with self.database.session() as session:
            parts = list(
                session.scalars(
                    select(SearchDocumentRecord.text)
                    .where(SearchDocumentRecord.video_id == video_id)
                    .order_by(SearchDocumentRecord.start_ms, SearchDocumentRecord.id)
                ).all()
            )
        return "\n".join(parts)[: self.settings.triage_max_text_chars]

    def _representative_frames(self, video_id: str) -> tuple[MediaReference, ...]:
        if self.settings.triage_max_frames == 0:
            return ()
        with self.database.session() as session:
            records = list(
                session.scalars(
                    select(ArtifactRecord)
                    .where(
                        ArtifactRecord.video_id == video_id,
                        ArtifactRecord.type == "KEYFRAME",
                    )
                    .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
                ).all()
            )
        if not records:
            return ()
        count = min(self.settings.triage_max_frames, len(records))
        indexes = (
            [0]
            if count == 1
            else [round(index * (len(records) - 1) / (count - 1)) for index in range(count)]
        )
        store = LocalArtifactStore(self.settings.artifact_root)
        references: list[MediaReference] = []
        for index in indexes:
            path = store.resolve(records[index].uri)
            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            references.append(MediaReference(kind="image", uri=f"data:{mime};base64,{encoded}"))
        return tuple(references)

    def _set_video_status(self, video_id: str, status: str) -> None:
        with self.database.session() as session:
            video = session.get(VideoRecord, video_id)
            if video is not None:
                video.triage_status = status

    def _fail_check(self, video_id: str, policy: CompiledPolicy, code: str) -> None:
        with self.database.session() as session:
            check = session.scalar(
                select(VideoTriageCheckRecord).where(
                    VideoTriageCheckRecord.video_id == video_id,
                    VideoTriageCheckRecord.policy_id == policy.policy_id,
                    VideoTriageCheckRecord.policy_version == policy.version,
                )
            )
            if check is not None:
                check.status = "FAILED"
                check.error_code = code
                check.updated_at = utc_now()

    def _record_model_call(
        self,
        call_id: str,
        job_id: str,
        video_id: str,
        request_key: str,
        profile: str,
        requested_model: str,
        actual_model: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        latency_ms: int,
        provider_request_id: str | None,
        status: str,
        error_code: str | None,
        policy: CompiledPolicy,
        has_images: bool,
    ) -> str:
        now = utc_now()
        with self.database.session() as session:
            record = session.scalar(
                select(ModelCallRecord).where(
                    ModelCallRecord.video_id == video_id,
                    ModelCallRecord.request_key == request_key,
                )
            )
            if record is None:
                record = ModelCallRecord(
                    id=call_id,
                    job_id=job_id,
                    run_id=None,
                    case_id=None,
                    video_id=video_id,
                    node="TRIAGE",
                    state_version=0,
                    role="triage",
                    profile=profile,
                    requested_model=requested_model,
                    actual_model=actual_model,
                    request_key=request_key,
                    request_summary={
                        "schema": "TriageOutput",
                        "policy_id": policy.policy_id,
                        "policy_version": policy.version,
                        "media_types": ["text", "image"] if has_images else ["text"],
                    },
                    response_payload=None,
                    status=status,
                    attempt=1,
                    lease_until=None,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                    provider_request_id=provider_request_id,
                    error_code=error_code,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                record.actual_model = actual_model
                record.status = status
                record.attempt += 1
                record.prompt_tokens = prompt_tokens
                record.completion_tokens = completion_tokens
                record.total_tokens = total_tokens
                record.latency_ms = latency_ms
                record.provider_request_id = provider_request_id
                record.error_code = error_code
                record.updated_at = now
            session.flush()
            return record.id


def _mock_output(policy: CompiledPolicy, text: str) -> TriageOutput:
    normalized = normalize_text(text)
    matched = [term for term in policy.trigger_terms if normalize_text(term) in normalized]
    return TriageOutput(
        action=TriageAction.CREATE_CASE if matched else TriageAction.SKIP,
        confidence=1.0,
        reason_code="TRIGGER_TERM_MATCHED" if matched else "NO_TRIGGER_TERM_MATCH",
        matched_terms=matched,
        matched_requirement_keys=(
            [
                item.requirement_key
                for item in policy.requirements
                if item.source_kind == "requirement"
            ]
            if matched
            else []
        ),
        summary="Deterministic Mock triage result.",
    )


def _validate_output(policy: CompiledPolicy, output: TriageOutput) -> None:
    allowed_terms = set(policy.trigger_terms)
    allowed_requirements = {item.requirement_key for item in policy.requirements}
    if not set(output.matched_terms) <= allowed_terms:
        raise ModelErrorOutput("triage returned unknown trigger terms")
    if not set(output.matched_requirement_keys) <= allowed_requirements:
        raise ModelErrorOutput("triage returned unknown Requirement keys")


class ModelErrorOutput(TriageError):
    def __init__(self, message: str) -> None:
        super().__init__("MODEL_OUTPUT_INVALID", message, retryable=False)


def _prompt(policy: CompiledPolicy, text: str) -> str:
    requirements = [
        {
            "key": item.requirement_key,
            "description": item.description,
            "source_kind": item.source_kind,
        }
        for item in policy.requirements
    ]
    return (
        f"Policy: {policy.policy_id} v{policy.version}\n"
        f"Trigger terms: {policy.trigger_terms}\nRequirements: {requirements}\n"
        f"Video summary:\n{text}"
    )
