# Evidence model

Stage 2 establishes the persistence contracts used by later investigation and governance
stages. Stage 4 appends provenance-checked Evidence but still does not aggregate evidence or
produce formal moderation decisions.

- A Case binds one Video to one published Policy version and model profile.
- Policy requirements and exceptions become explicit case-scoped Requirement rows.
- Evidence records require a source, a valid millisecond range and a controlled stance.
- RequirementResult and Decision records keep Evidence links in association tables.
- Published policies, Evidence, RequirementResult and Decision records are append-only at
  the service boundary.
- ToolRun may change from `running` to a terminal state and must bind to an AgentRun from the
  same Case.
- Evidence that cites a ModelCall must cite a successful call from the same Case. The database
  also checks Artifact ownership and ToolRun Requirement ownership.
- AgentStep and completed Evidence remain append-only. AgentRun alone changes as each
  optimistic checkpoint advances its state version.
- Stage 4 stores provisional verdicts on AgentRun. RequirementResult and Decision remain empty
  until Stage 5 performs deterministic aggregation and rule evaluation.

Cases are unique by video, policy ID and policy version. Creating the Case and all its
Requirements occurs in one transaction.
