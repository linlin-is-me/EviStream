"""Run the real media, Embedding, tool, and Agent path in a disposable database."""

import asyncio
import json
from pathlib import Path

from sqlalchemy import func, select

from evistream.agent.runtime import build_agent_runtime
from evistream.config import get_settings
from evistream.media.runtime import build_media_runtime
from evistream.models import resolve_embedding_gateway
from evistream.policies.schema import load_policy
from evistream.policies.versioning import CaseApplicationService, PolicyVersionService
from evistream.retrieval import EmbeddingIndexService
from evistream.storage.database import Database
from evistream.storage.models import (
    AgentStepRecord,
    ArtifactRecord,
    EvidenceRecord,
    ModelCallRecord,
    ToolRunRecord,
)


def main() -> None:
    settings = get_settings()
    if settings.model_profile == "mock":
        raise RuntimeError("external acceptance requires a non-Mock model profile")
    if (
        settings.asr_backend != "faster-whisper"
        or settings.ocr_backend != "paddleocr"
        or settings.vision_backend != "gateway"
    ):
        raise RuntimeError("external acceptance requires real ASR, OCR, and vision backends")
    database = Database(settings.database_url)
    media = build_media_runtime(settings)
    video, job = media.service.register_file(Path("tests/fixtures/media/stage0_sample.mp4"))
    media_execution = asyncio.run(
        media.dispatcher.dispatch(media.service.build_job_request(job.job_id))
    )
    if media_execution.error_code:
        raise RuntimeError(f"media preprocessing failed: {media_execution.error_code}")
    video = media.service.get_video(video.video_id)
    policy_service = PolicyVersionService(database)
    policy = policy_service.publish(load_policy(Path("configs/policies/violence-weapon-v1.yaml")))
    case = CaseApplicationService(database).create_case(
        video.video_id,
        policy.policy_id,
        policy.version,
        settings.model_profile,
    ).case
    embedding, profile = resolve_embedding_gateway(
        settings.model_config_dir,
        settings.model_profile,
        environment=settings.model_environment(),
    )
    index = asyncio.run(
        EmbeddingIndexService(database, embedding, profile).index_video(video.video_id)
    )
    if index.status != "success":
        raise RuntimeError(f"Embedding indexing failed: {index.error_code}")
    runtime = build_agent_runtime(settings, settings.model_profile)
    request = runtime.service.prepare(case.case_id)
    execution = asyncio.run(runtime.dispatcher.dispatch(request))
    if execution.error_code:
        raise RuntimeError(f"investigation failed: {execution.error_code}")
    result = runtime.service.get_result(str(request.payload["run_id"]))
    with database.session() as session:
        calls = session.scalars(
            select(ModelCallRecord).where(ModelCallRecord.run_id == result.run_id)
        ).all()
        tool_count = session.scalar(
            select(func.count()).select_from(ToolRunRecord).where(
                ToolRunRecord.run_id == result.run_id
            )
        ) or 0
        evidence_count = session.scalar(
            select(func.count())
            .select_from(EvidenceRecord)
            .join(ModelCallRecord, EvidenceRecord.model_call_id == ModelCallRecord.id)
            .where(ModelCallRecord.run_id == result.run_id)
        ) or 0
        step_count = session.scalar(
            select(func.count()).select_from(AgentStepRecord).where(
                AgentStepRecord.run_id == result.run_id
            )
        ) or 0
        artifact_count = session.scalar(
            select(func.count()).select_from(ArtifactRecord).where(
                ArtifactRecord.video_id == video.video_id
            )
        ) or 0
    if not calls or not tool_count or not evidence_count or not step_count:
        raise RuntimeError("external investigation did not persist a complete trace")
    roles = sorted({item.role for item in calls})
    models = sorted({item.actual_model or item.requested_model for item in calls})
    print(
        json.dumps(
            {
                "status": result.status,
                "stop_reason": result.stop_reason,
                "models": models,
                "roles": roles,
                "prompt_tokens": sum(item.prompt_tokens for item in calls),
                "completion_tokens": sum(item.completion_tokens for item in calls),
                "total_tokens": sum(item.total_tokens for item in calls),
                "model_latency_ms": sum(item.latency_ms for item in calls),
                "node_count": step_count,
                "tool_count": tool_count,
                "evidence_count": evidence_count,
                "artifact_count": artifact_count,
                "embedding_model": index.actual_model,
                "embedding_dimensions": index.dimensions,
                "embedding_tokens": index.prompt_tokens,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
