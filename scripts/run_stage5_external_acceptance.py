"""Run real formal governance and REINVESTIGATE in a disposable database."""

import asyncio
import json
from hashlib import sha256
from pathlib import Path

import yaml
from sqlalchemy import func, select

from evistream.agent.runtime import build_agent_runtime
from evistream.config import get_settings
from evistream.governance.runtime import build_governance_runtime
from evistream.media.runtime import build_media_runtime
from evistream.models import resolve_embedding_gateway
from evistream.policies.schema import LoadedPolicy, load_policy
from evistream.policies.versioning import CaseApplicationService, PolicyVersionService
from evistream.retrieval import EmbeddingIndexService
from evistream.storage.database import Database
from evistream.storage.models import (
    AgentStepRecord,
    EvidenceRecord,
    ModelCallRecord,
    ReplayItemRecord,
    ReplayLineageRecord,
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
        raise RuntimeError("external acceptance requires real media backends")
    database = Database(settings.database_url)
    media = build_media_runtime(settings)
    video, media_job = media.service.register_file(
        Path("tests/fixtures/media/stage0_sample.mp4")
    )
    media_execution = asyncio.run(
        media.dispatcher.dispatch(media.service.build_job_request(media_job.job_id))
    )
    if media_execution.error_code:
        raise RuntimeError(f"media preprocessing failed: {media_execution.error_code}")
    video = media.service.get_video(video.video_id)

    versions = PolicyVersionService(database)
    source_loaded = load_policy(Path("configs/policies/violence-weapon-v1.yaml"))
    source_policy = versions.publish(source_loaded)
    source_case = CaseApplicationService(database).create_case(
        video.video_id,
        source_policy.policy_id,
        source_policy.version,
        settings.model_profile,
    ).case
    embedding, embedding_profile = resolve_embedding_gateway(
        settings.model_config_dir,
        settings.model_profile,
        environment=settings.model_environment(),
    )
    index = asyncio.run(
        EmbeddingIndexService(database, embedding, embedding_profile).index_video(
            video.video_id
        )
    )
    if index.status != "success":
        raise RuntimeError(f"Embedding indexing failed: {index.error_code}")

    agent = build_agent_runtime(settings, settings.model_profile)
    source_request = agent.service.prepare(source_case.case_id)
    source_execution = asyncio.run(agent.dispatcher.dispatch(source_request))
    if source_execution.error_code:
        raise RuntimeError(f"source investigation failed: {source_execution.error_code}")
    governance_runtime = build_governance_runtime(settings)
    source_decision = governance_runtime.governance.finalize_case(source_case.case_id)

    target_document = source_loaded.document.model_copy(deep=True)
    target_document.version = 2
    target_document.requirements[0].description += "并能清晰辨识"
    source_yaml = yaml.safe_dump(
        target_document.model_dump(mode="json"), allow_unicode=True, sort_keys=False
    )
    target_policy = versions.publish(
        LoadedPolicy(
            document=target_document,
            source_yaml=source_yaml,
            source_sha256=sha256(source_yaml.encode()).hexdigest(),
        )
    )
    preview = governance_runtime.planner.preview(
        source_policy.policy_id, source_policy.version, target_policy.version
    )
    if preview.mode != "REINVESTIGATE":
        raise RuntimeError("semantic Requirement change did not select REINVESTIGATE")
    runtime = build_governance_runtime(settings, settings.model_profile)
    replay_request = runtime.replay.prepare(
        source_policy.policy_id,
        source_policy.version,
        target_policy.version,
        preview.preview_sha256,
        model_profile=settings.model_profile,
    )
    replay_execution = asyncio.run(runtime.dispatcher.dispatch(replay_request))
    if replay_execution.error_code:
        raise RuntimeError(f"replay failed: {replay_execution.error_code}")
    replay_result = runtime.replay.status(replay_request.job_id)

    with database.session() as session:
        replay_item = session.scalar(
            select(ReplayItemRecord).where(
                ReplayItemRecord.replay_job_id
                == replay_request.payload["replay_job_id"]
            )
        )
        if replay_item is None or replay_item.target_case_id is None:
            raise RuntimeError("replay did not materialize a target Case")
        target_calls = list(
            session.scalars(
                select(ModelCallRecord).where(
                    ModelCallRecord.case_id == replay_item.target_case_id
                )
            ).all()
        )
        counts = {
            "steps": int(
                session.scalar(
                    select(func.count(func.distinct(AgentStepRecord.id)))
                    .select_from(AgentStepRecord)
                    .join(ModelCallRecord, ModelCallRecord.run_id == AgentStepRecord.run_id)
                    .where(ModelCallRecord.case_id == replay_item.target_case_id)
                )
                or 0
            ),
            "tools": int(
                session.scalar(
                    select(func.count()).select_from(ToolRunRecord).where(
                        ToolRunRecord.case_id == replay_item.target_case_id
                    )
                )
                or 0
            ),
            "evidence": int(
                session.scalar(
                    select(func.count()).select_from(EvidenceRecord).where(
                        EvidenceRecord.case_id == replay_item.target_case_id
                    )
                )
                or 0
            ),
            "lineage": int(
                session.scalar(
                    select(func.count()).select_from(ReplayLineageRecord).where(
                        ReplayLineageRecord.replay_item_id == replay_item.id
                    )
                )
                or 0
            ),
        }
        target_decision = replay_item.target_decision_id
        replay_status = replay_item.status
    if not target_calls or not target_decision:
        raise RuntimeError("real REINVESTIGATE did not persist calls and a Decision")
    print(
        json.dumps(
            {
                "source_verdict": source_decision.verdict,
                "target_status": replay_status,
                "replay": (
                    replay_result.model_dump(mode="json")
                    if hasattr(replay_result, "model_dump")
                    else replay_result
                ),
        "models": sorted(
                    {call.actual_model or call.requested_model for call in target_calls}
                ),
                "roles": sorted({call.role for call in target_calls}),
                "model_calls": len(target_calls),
                "prompt_tokens": sum(call.prompt_tokens for call in target_calls),
                "completion_tokens": sum(call.completion_tokens for call in target_calls),
                "total_tokens": sum(call.total_tokens for call in target_calls),
                "model_latency_ms": sum(call.latency_ms for call in target_calls),
                "embedding_model": index.actual_model,
                "embedding_dimensions": index.dimensions,
                "embedding_tokens": index.prompt_tokens,
                **counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
