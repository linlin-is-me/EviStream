# Evidence model

Stage 2 establishes the persistence contracts used by later investigation and governance
stages. It does not aggregate evidence or produce moderation decisions.

- A Case binds one Video to one published Policy version and model profile.
- Policy requirements and exceptions become explicit case-scoped Requirement rows.
- Evidence records require a source, a valid millisecond range and a controlled stance.
- RequirementResult and Decision records keep Evidence links in association tables.
- Published policies, Evidence, RequirementResult and Decision records are append-only at
  the service boundary.
- ToolRun may change from `running` to a terminal state. Its Agent Run foreign key enters
  Stage 4.

Cases are unique by video, policy ID and policy version. Creating the Case and all its
Requirements occurs in one transaction.
