# Agent runtime

Stage 4 investigates an existing Case with a runtime-controlled graph:

```text
PLAN -> RETRIEVE -> INSPECT -> VERIFY -> CHALLENGE -> PLAN
                                              |          |
                                              +------> DECIDE
```

The model proposes typed outputs. The runtime owns transitions, tool selection validation,
budgets, provenance checks and terminal status. Planner actions must target a Requirement from
the current Case and use one of that Requirement's declared tool capabilities. Retrieve and
Challenge call the Stage 3 ToolExecutor, so request-key reuse and ToolRun persistence remain
unchanged.

Inspect samples at most four frames from selected intervals and sends them only to the Triage
role. Verify receives the Requirement, selected ToolItems, observations and the same bounded
frame set. Evidence is accepted only when its source reference and time interval fall within a
ToolItem. The Evidence row, immutable AgentStep and next checkpoint commit in one transaction.

## Persistence and recovery

AgentRun holds the current snapshot, state version, lease, counters and provisional outcome.
Each completed node appends one AgentStep. An optimistic update on `run_id + state_version`
allows only one executor to advance a checkpoint. A worker that loses its process after commit
can reclaim an expired lease and continue from `next_node`; completed ToolRun, ModelCall,
Evidence and AgentStep records are reused.

AuditedModelGateway wraps both Mock and OpenAI-compatible gateways. A model request record
contains role, Schema name, message lengths, media types and SHA-256 hashes. It excludes API
keys, prompts, media Data URI values and vectors. Successful terminal calls are reusable by
request key. A provider response followed by a process crash before database commit remains an
at-least-once boundary.

## Budgets and outcomes

Defaults allow six investigation rounds, eight Triage or Verifier calls, three consecutive
tool failures, two stagnant rounds and 300 seconds. Each successful node refreshes the lease.
Missing mandatory evidence, contradictory stances or an exhausted budget forces
`NEEDS_HUMAN_REVIEW`. This remains a successful technical Job. Invalid transitions,
checkpoints, database state or planner actions fail both Job and AgentRun.

An approval or rejection is provisional and moves the Case to `INVESTIGATED`. Stage 4 never
writes RequirementResult or Decision; Stage 5 will own aggregation and deterministic policy
evaluation.
