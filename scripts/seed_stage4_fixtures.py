"""Seed deterministic orchestration fixtures into a disposable Stage 4 database."""

import json
from pathlib import Path

from evistream.config import get_settings
from evistream.retrieval.text import normalize_text, search_lexemes
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    ArtifactRecord,
    CaseRecord,
    PolicyRecord,
    RequirementRecord,
    SearchDocumentRecord,
    VideoRecord,
)


def main() -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    artifacts = LocalArtifactStore(settings.artifact_root)
    source = Path("tests/fixtures/media/stage0_sample.mp4")
    fixtures = {
        "obvious": [("doc_stage4_obvious", "weapon threat is visible", 0, 7_720)],
        "cross_segment": [
            ("doc_stage4_cross_one", "weapon appears", 0, 7_720),
            ("doc_stage4_cross_two", "weapon threat follows", 18_300, 30_000),
        ],
        "insufficient": [
            ("doc_stage4_insufficient", "unrelated educational text", 0, 7_720)
        ],
    }
    for scenario, documents in fixtures.items():
        _seed(database, artifacts, source, scenario, documents)
    print(
        json.dumps(
            {"case_ids": [f"case_stage4_{name}" for name in fixtures]},
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _seed(
    database: Database,
    artifacts: LocalArtifactStore,
    source: Path,
    scenario: str,
    documents: list[tuple[str, str, int, int]],
) -> None:
    video_id = f"vid_stage4_{scenario}"
    case_id = f"case_stage4_{scenario}"
    requirement_id = f"req_stage4_{scenario}"
    policy_id = f"test.stage4.runtime.{scenario}"
    source_uri = artifacts.put_file(source, f"videos/{video_id}/source.mp4")
    transcript_id = f"art_stage4_{scenario}"
    now = utc_now()
    with database.session() as session:
        session.add(
            VideoRecord(
                id=video_id,
                original_name=source.name,
                artifact_uri=source_uri,
                fingerprint=None,
                duration_ms=30_000,
                width=640,
                height=360,
                container="mp4",
                video_codec="h264",
                has_audio=True,
                audio_codec="aac",
                status="READY",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            ArtifactRecord(
                id=transcript_id,
                video_id=video_id,
                type="TRANSCRIPT",
                uri=artifacts.uri_for_key(f"videos/{video_id}/transcript.json"),
                artifact_metadata={"fixture": True},
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            PolicyRecord(
                policy_id=policy_id,
                version=1,
                name=f"Stage 4 {scenario}",
                severity="HIGH",
                enabled=True,
                lifecycle="PUBLISHED",
                source_yaml="id: controlled-stage4-fixture",
                compiled_policy={},
                source_sha256="1" * 64,
                semantic_sha256="2" * 64,
                compiler_version="stage4-fixture",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            CaseRecord(
                id=case_id,
                video_id=video_id,
                policy_id=policy_id,
                policy_version=1,
                model_profile="mock",
                status="READY",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            RequirementRecord(
                id=requirement_id,
                case_id=case_id,
                requirement_key="weapon_presence",
                requirement_type="speech_content",
                source_kind="requirement",
                required=True,
                description="Determine whether a weapon is present",
                suggested_queries=["weapon"],
                modalities=["transcript"],
                tool_capabilities=["search_transcript"],
                semantic_sha256="3" * 64,
                status="PENDING",
                created_at=now,
                updated_at=now,
            )
        )
        for document_id, content, start_ms, end_ms in documents:
            session.add(
                SearchDocumentRecord(
                    id=document_id,
                    video_id=video_id,
                    segment_id=None,
                    artifact_id=transcript_id,
                    modality="transcript",
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=content,
                    normalized_text=normalize_text(content),
                    keyword_lexemes=search_lexemes(content),
                    embedding=None,
                    created_at=now,
                    updated_at=now,
                )
            )


if __name__ == "__main__":
    main()
