"""Composition root for Stage 5 governance and replay commands."""

from dataclasses import dataclass

from evistream.agent.runtime import AgentRuntime, build_agent_runtime
from evistream.application import HandlerRegistry, InlineExecutor, PolicyReplayJobHandler
from evistream.config import Settings
from evistream.governance.review import HumanGovernanceService
from evistream.governance.service import GovernanceApplicationService
from evistream.governance.timeline import CaseTimelineService
from evistream.replay.planner import ReplayPlanner
from evistream.replay.service import ReplayApplicationService
from evistream.storage.database import Database


@dataclass(frozen=True)
class GovernanceRuntime:
    database: Database
    governance: GovernanceApplicationService
    human: HumanGovernanceService
    timeline: CaseTimelineService
    planner: ReplayPlanner
    replay: ReplayApplicationService
    dispatcher: InlineExecutor


def build_governance_runtime(
    settings: Settings,
    profile_name: str | None = None,
) -> GovernanceRuntime:
    database = Database(settings.database_url)
    agent: AgentRuntime | None = None
    if profile_name is not None:
        agent = build_agent_runtime(settings, profile_name)
        database = agent.database
    governance = GovernanceApplicationService(database)
    human = HumanGovernanceService(database)
    timeline = CaseTimelineService(database)
    planner = ReplayPlanner(database)
    replay = ReplayApplicationService(
        database,
        planner,
        governance,
        agent.service if agent is not None else None,
        agent.dispatcher if agent is not None else None,
    )
    handlers = HandlerRegistry()
    handlers.register("POLICY_REPLAY", PolicyReplayJobHandler(replay))
    return GovernanceRuntime(
        database=database,
        governance=governance,
        human=human,
        timeline=timeline,
        planner=planner,
        replay=replay,
        dispatcher=InlineExecutor(handlers),
    )
