import os
from pathlib import Path

import pytest
import yaml
from sqlalchemy import delete, func, select

from evistream.domain import PolicyLifecycle
from evistream.policies.schema import load_policy
from evistream.policies.seeds import apply_demo_seeds, load_case_seeds
from evistream.policies.versioning import (
    CaseApplicationService,
    PolicyVersionError,
    PolicyVersionService,
)
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    CaseRecord,
    PolicyRecord,
    RequirementRecord,
    VideoRecord,
)


@pytest.mark.integration
def test_policy_versions_and_case_requirements_are_persistent(tmp_path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    database = Database(database_url)
    with database.session() as session:
        session.execute(delete(RequirementRecord))
        session.execute(delete(CaseRecord))
        session.execute(delete(PolicyRecord))
        session.execute(delete(VideoRecord))
        now = utc_now()
        session.add(
            VideoRecord(
                id="vid_stage2",
                original_name="stage2.mp4",
                artifact_uri="artifact://stage2/source.mp4",
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

    source = load_policy(Path("configs/policies/violence-weapon-v1.yaml"))
    versions = PolicyVersionService(database)
    assert versions.save_draft(source).lifecycle == PolicyLifecycle.DRAFT
    assert versions.save_draft(source).lifecycle == PolicyLifecycle.DRAFT
    published = versions.publish(source)
    assert published.lifecycle == PolicyLifecycle.PUBLISHED
    assert versions.publish(source).semantic_sha256 == published.semantic_sha256
    assert len(versions.list_versions(source.document.id)) == 1

    changed = tmp_path / "changed.yaml"
    changed.write_text(
        source.source_yaml.replace("暴力与武器展示审核", "修改后的名称"),
        encoding="utf-8",
    )
    with pytest.raises(PolicyVersionError) as conflict:
        versions.publish(load_policy(changed))
    assert conflict.value.code == "POLICY_VERSION_CONFLICT"

    cases = CaseApplicationService(database)
    bundle = cases.create_case(
        "vid_stage2",
        source.document.id,
        source.document.version,
        "mock",
        case_id="case_stage2",
    )
    duplicate = cases.create_case(
        "vid_stage2",
        source.document.id,
        source.document.version,
        "mock",
    )
    assert duplicate.case.case_id == bundle.case.case_id
    assert len(bundle.requirements) == 5
    assert {item.source_kind for item in bundle.requirements} == {"requirement", "exception"}
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(CaseRecord)) == 1
        assert session.scalar(select(func.count()).select_from(RequirementRecord)) == 5


@pytest.mark.integration
def test_demo_seed_apply_is_transactional_and_idempotent(tmp_path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    database = Database(database_url)
    manifest_path = Path("configs/demo/stage2-cases.yaml")
    manifest = load_case_seeds(manifest_path)
    mapping: dict[str, str] = {}
    with database.session() as session:
        session.execute(delete(RequirementRecord))
        session.execute(delete(CaseRecord))
        session.execute(delete(PolicyRecord))
        session.execute(delete(VideoRecord))
        now = utc_now()
        for index, fixture_ref in enumerate(sorted({item.fixture_ref for item in manifest.cases})):
            video_id = f"vid_seed_{index}"
            mapping[fixture_ref] = video_id
            session.add(
                VideoRecord(
                    id=video_id,
                    original_name=f"{fixture_ref}.mp4",
                    artifact_uri=f"artifact://stage2/{fixture_ref}.mp4",
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
    map_path = tmp_path / "video-map.yaml"
    map_path.write_text(
        yaml.safe_dump({"videos": mapping}, allow_unicode=True), encoding="utf-8"
    )
    first = apply_demo_seeds(
        database,
        Path("configs/policies"),
        manifest_path,
        map_path,
        model_profile="mock",
    )
    second = apply_demo_seeds(
        database,
        Path("configs/policies"),
        manifest_path,
        map_path,
        model_profile="mock",
    )
    assert len(first.materialized_case_ids) == 9
    assert second.materialized_case_ids == first.materialized_case_ids
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(PolicyRecord)) == 3
        assert session.scalar(select(func.count()).select_from(CaseRecord)) == 9
        assert session.scalar(select(func.count()).select_from(RequirementRecord)) == 45
