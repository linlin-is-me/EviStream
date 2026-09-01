"""Exercise the public Stage 6 workflow against a running Compose deployment."""

import os
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import httpx

BASE_URL = os.environ.get("EVISTREAM_VERIFY_BASE_URL", "http://127.0.0.1:8000")
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        if _expect(client.get("/api/v1/ready"))["status"] != "ready":
            raise RuntimeError("deployment is not ready")
        _expect(client.get("/api/v1/model-profiles/mock/health"))
        policy_id = "test.stage6.compose"
        _publish(client, _policy(policy_id, 1, "HIGH", "Detect Stage 6 transcript evidence"))

        sample = Path("tests/fixtures/media/stage0_sample.mp4")
        with tempfile.NamedTemporaryFile(suffix=".mp4") as upload:
            upload.write(sample.read_bytes())
            upload.write(b"EVISTREAM_STAGE6_DEPLOY_V1")
            upload.flush()
            upload.seek(0)
            submission = _expect(
                client.post(
                    "/api/v1/videos",
                    files={"file": (sample.name, upload, "video/mp4")},
                    data={"model_profile": "mock"},
                    headers={"Idempotency-Key": "verify-deploy-video-v1"},
                )
            )
        video_id = submission["video"]["video_id"]
        _wait_job(client, submission["job"]["job_id"])
        video = _expect(client.get(f"/api/v1/videos/{video_id}"))
        cases = video["cases"]
        if not cases:
            raise RuntimeError("automatic triage did not create a Case")
        case_id = cases[0]["case_id"]

        investigation = _expect(
            client.post(f"/api/v1/cases/{case_id}/investigate", json={})
        )
        _wait_job(client, investigation["job"]["job_id"])
        case = _expect(client.get(f"/api/v1/cases/{case_id}"))
        if case["current_decision"] is None:
            raise RuntimeError("investigation did not create a formal Decision")

        _expect(
            client.post(
                f"/api/v1/cases/{case_id}/reviews",
                json={
                    "reviewer": "compose-verifier",
                    "verdict": "NEEDS_HUMAN_REVIEW",
                    "note": "deployment verification review",
                    "evidence_ids": [],
                },
                headers={"Idempotency-Key": "verify-deploy-review-v1"},
            )
        )
        appeal = _expect(
            client.post(
                f"/api/v1/cases/{case_id}/appeals",
                json={
                    "submitter": "compose-verifier",
                    "statement": "deployment verification appeal",
                },
                headers={"Idempotency-Key": "verify-deploy-appeal-v1"},
            )
        )
        _expect(
            client.post(
                f"/api/v1/cases/{case_id}/appeals/{appeal['appeal_id']}/resolve",
                json={
                    "reviewer": "compose-verifier",
                    "verdict": "NEEDS_HUMAN_REVIEW",
                    "note": "deployment verification resolution",
                    "evidence_ids": [],
                },
                headers={"Idempotency-Key": "verify-deploy-appeal-resolution-v1"},
            )
        )

        _publish(client, _policy(policy_id, 2, "CRITICAL", "Detect Stage 6 transcript evidence"))
        _run_replay(client, policy_id, 1, 2)
        _publish(client, _policy(policy_id, 3, "CRITICAL", "Detect changed Stage 6 evidence"))
        _run_replay(client, policy_id, 2, 3)

        range_response = client.get(
            f"/api/v1/videos/{video_id}/content", headers={"Range": "bytes=0-31"}
        )
        if range_response.status_code != 206 or len(range_response.content) != 32:
            raise RuntimeError("video Range response is invalid")
        _expect(client.get(f"/api/v1/cases/{case_id}/timeline"))
        print(
            {
                "status": "passed",
                "video_id": video_id,
                "case_id": case_id,
                "workflows": ["triage", "investigation", "review", "appeal", "replay"],
            }
        )


def _publish(client: httpx.Client, source: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/policies", json={"source_yaml": source, "lifecycle": "published"}
    )
    if response.status_code == 409:
        return cast(dict[str, Any], response.json())
    return cast(dict[str, Any], _expect(response))


def _run_replay(client: httpx.Client, policy_id: str, source: int, target: int) -> None:
    preview = _expect(
        client.post(
            f"/api/v1/policies/{policy_id}/replay/preview",
            json={"from_version": source, "to_version": target},
        )
    )
    submission = _expect(
        client.post(
            f"/api/v1/policies/{policy_id}/replay",
            json={
                "from_version": source,
                "to_version": target,
                "preview_sha256": preview["preview_sha256"],
                "model_change_policy": "keep",
            },
        )
    )
    _wait_job(client, submission["job"]["job_id"])
    _expect(client.get(f"/api/v1/replay-jobs/{submission['job']['job_id']}/diff"))


def _wait_job(client: httpx.Client, job_id: str, timeout: float = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = _expect(client.get(f"/api/v1/jobs/{job_id}"))
        if job["status"] in TERMINAL:
            if job["status"] != "SUCCEEDED":
                raise RuntimeError(f"job {job_id} ended as {job['status']}: {job}")
            return cast(dict[str, Any], job)
        time.sleep(2)
    raise TimeoutError(f"job did not finish: {job_id}")


def _expect(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
    return response.json()


def _policy(policy_id: str, version: int, severity: str, description: str) -> str:
    return f"""id: {policy_id}
version: {version}
name: Stage 6 Compose verification
enabled: true
severity: {severity}
trigger_terms:
  - EviStream
requirements:
  - id: transcript_match
    type: speech_content
    required: true
    description: {description}
decision:
  reject_when:
    all:
      - transcript_match
  escalate_when:
    any:
      - contradictory_evidence
"""


if __name__ == "__main__":
    main()
