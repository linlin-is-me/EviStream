"""Construct one shared Agent runtime for CLI and future queue workers."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from evistream.agent.audit import AuditedModelGateway
from evistream.agent.engine import InvestigationEngine
from evistream.agent.media import InspectionFrameSampler
from evistream.agent.mock import ScriptedAgentMockGateway, ScriptedResponses
from evistream.agent.service import AgentInvestigationService
from evistream.agent.types import AgentNode, InvestigationState
from evistream.application import (
    AgentInvestigationJobHandler,
    HandlerRegistry,
    InlineExecutor,
)
from evistream.config import Settings
from evistream.media.runtime import MediaAdapterUnavailable
from evistream.models import ModelRole, build_model_gateway, resolve_embedding_gateway
from evistream.models.profiles import load_model_profile, resolve_model_profile
from evistream.models.types import ModelGateway
from evistream.retrieval import HybridRetrievalService
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database
from evistream.tools import ToolExecutor, build_default_registry


@dataclass(frozen=True)
class AgentRuntime:
    database: Database
    service: AgentInvestigationService
    dispatcher: InlineExecutor


def build_agent_runtime(
    settings: Settings,
    profile_name: str,
    *,
    checkpoint_hook: Callable[[InvestigationState], None] | None = None,
) -> AgentRuntime:
    database = Database(settings.database_url)
    artifacts = LocalArtifactStore(settings.artifact_root)
    profile = load_model_profile(settings.model_config_dir, profile_name)
    environment = settings.model_environment()
    resolved = {
        role: resolve_model_profile(profile, role, environment)
        for role in (ModelRole.AGENT, ModelRole.TRIAGE, ModelRole.VERIFIER)
    }
    gateways: dict[ModelRole, ModelGateway]
    if profile.gateway == "mock":
        script = _mock_script()
        shared = ScriptedResponses.load(script) if script is not None else ScriptedResponses()
        mock = ScriptedAgentMockGateway(shared)
        gateways = {role: mock for role in resolved}
    else:
        gateways = {
            role: build_model_gateway(
                settings.model_config_dir,
                profile_name,
                role,
                environment=environment,
            )
            for role in resolved
        }
    embedding, embedding_profile = resolve_embedding_gateway(
        settings.model_config_dir,
        profile_name,
        environment=environment,
    )
    retrieval = HybridRetrievalService(
        database,
        embedding,
        embedding_profile,
        rrf_k=settings.retrieval_rrf_k,
        candidate_limit=settings.retrieval_candidate_limit,
    )
    registry = build_default_registry(database, artifacts, settings, retrieval)
    tools = ToolExecutor(database, registry)
    service = AgentInvestigationService(database, settings, checkpoint_hook)
    frames = InspectionFrameSampler(database, artifacts, settings)

    def engine_factory(state: InvestigationState) -> InvestigationEngine:
        def gateway_factory(
            role: ModelRole, node: AgentNode, state_version: int
        ) -> AuditedModelGateway:
            return AuditedModelGateway(
                database,
                gateways[role],
                run_id=state.run_id,
                case_id=state.case_id,
                job_id=state.job_id,
                node=node,
                state_version=state_version,
                profile=profile_name,
                requested_model=resolved[role].model,
                lease_seconds=settings.agent_job_lease_seconds,
            )

        return InvestigationEngine(
            database,
            settings,
            service,
            tools,
            frames,
            gateway_factory,
        )

    handlers = HandlerRegistry()
    handlers.register(
        "AGENT_INVESTIGATION",
        AgentInvestigationJobHandler(service, engine_factory),
    )
    return AgentRuntime(database, service, InlineExecutor(handlers))


def _mock_script() -> Path | None:
    value = os.environ.get("EVISTREAM_STAGE4_SCRIPT", "").strip()
    if not value:
        return None
    if os.environ.get("EVISTREAM_STAGE4_VERIFY") != "1":
        raise MediaAdapterUnavailable(
            "Stage 4 scripted responses are restricted to the verification entrypoint"
        )
    path = Path(value).resolve()
    if not path.is_file():
        raise MediaAdapterUnavailable(f"Stage 4 script not found: {path}")
    return path
