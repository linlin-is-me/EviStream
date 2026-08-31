from pathlib import Path

import pytest
from pydantic import ValidationError

from evistream.policies.compiler import PolicyCompiler
from evistream.policies.schema import PolicyDocument, PolicyError, load_policy
from evistream.policies.seeds import validate_demo_seeds

POLICY_DIR = Path("configs/policies")


def test_demo_policies_compile_normal_and_exception_requirements() -> None:
    for path in sorted(POLICY_DIR.glob("*.yaml")):
        compiled = PolicyCompiler().compile(load_policy(path))
        normal = [item for item in compiled.requirements if item.source_kind == "requirement"]
        exceptions = [item for item in compiled.requirements if item.source_kind == "exception"]
        assert len(normal) == 2
        assert len(exceptions) == 3
        assert all(item.requirement_key.startswith("exception.") for item in exceptions)
        assert len(compiled.semantic_sha256) == 64


def test_compilation_hash_ignores_yaml_whitespace(tmp_path) -> None:
    source = (POLICY_DIR / "violence-weapon-v1.yaml").read_text(encoding="utf-8")
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(source, encoding="utf-8")
    second.write_text(f"\n{source}\n", encoding="utf-8")

    first_loaded = load_policy(first)
    second_loaded = load_policy(second)
    compiler = PolicyCompiler()
    assert first_loaded.source_sha256 != second_loaded.source_sha256
    assert compiler.compile(first_loaded).semantic_sha256 == compiler.compile(
        second_loaded
    ).semantic_sha256


def test_policy_loader_rejects_duplicate_yaml_keys(tmp_path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text("id: one.rule\nid: two.rule\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="duplicate key"):
        load_policy(path)


def test_policy_loader_rejects_oversized_file(tmp_path) -> None:
    path = tmp_path / "large.yaml"
    path.write_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(PolicyError, match="256 KiB"):
        load_policy(path)


def test_policy_schema_rejects_unknown_condition_reference() -> None:
    payload = {
        "id": "test.policy",
        "version": 1,
        "name": "test",
        "enabled": True,
        "severity": "LOW",
        "trigger_terms": ["test"],
        "requirements": [
            {
                "id": "presence",
                "type": "visual_presence",
                "required": True,
                "description": "presence",
            }
        ],
        "exceptions": [],
        "decision": {
            "reject_when": {"all": ["missing"]},
            "escalate_when": {"any": ["contradictory_evidence"]},
        },
    }
    with pytest.raises(ValidationError, match="unknown references"):
        PolicyDocument.model_validate(payload)


def test_policy_schema_rejects_empty_boolean_expression() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        PolicyDocument.model_validate(
            {
                "id": "test.policy",
                "version": 1,
                "name": "test",
                "severity": "LOW",
                "trigger_terms": ["test"],
                "requirements": [
                    {
                        "id": "presence",
                        "type": "visual_presence",
                        "description": "presence",
                    }
                ],
                "decision": {
                    "reject_when": {"all": []},
                    "escalate_when": {"any": ["contradictory_evidence"]},
                },
            }
        )


def test_stage2_seed_manifest_has_expected_shape() -> None:
    summary = validate_demo_seeds(POLICY_DIR, Path("configs/demo/stage2-cases.yaml"))
    assert summary.policy_count == 3
    assert summary.case_count == 9
    assert summary.scenarios == {
        "clear_violation": 3,
        "context_exception": 3,
        "insufficient_evidence": 3,
    }
