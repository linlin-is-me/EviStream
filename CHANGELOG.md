# Changelog

All notable changes will be documented here. The format follows Keep a Changelog, and versions
follow Semantic Versioning.

## Unreleased

### Added

- Stage 5 Evidence aggregation, three-valued rule evaluation, and formal machine Decisions.
- Append-only human Reviews, Appeals, Case timelines, and current audit pointers.
- Policy diff, selective reevaluation or reinvestigation, replay lineage, and replay CLI commands.
- `verify-stage5` migration, governance, replay, static-analysis, and coverage gate.
- Stage 4 Agent runs, immutable steps, audited model calls and optimistic checkpoints.
- Runtime-owned Plan, Retrieve, Inspect, Verify, Challenge and Decide transitions.
- `investigate`, `investigation-status` and `investigation-trace` CLI commands.
- Deterministic three-path Agent fixtures and `verify-stage4` disposable-database gate.
- Shared media runtime factory and `MEDIA_PREPROCESS` Job Handler for API, CLI and future RQ use.
- Database enforcement for published-policy immutability and same-case evidence relationships.
- Structured embedding index failures and isolated Stage 1–3 verification databases.
- Stage 3 provider-neutral embeddings, hybrid temporal retrieval and eight core tools.
- Stage 2 moderation entities, policy compiler, rule versions and demo case metadata.
- Stage 1 PostgreSQL media pipeline, local artifacts and persistent media jobs.
- Stage 0 repository foundation is in progress.

### Changed

- ToolRun now binds to a same-Case AgentRun; Evidence model calls use a same-Case foreign key.
- Case status now distinguishes provisional `INVESTIGATED` results from formal decisions.
- Media jobs now use atomic claims, bounded upload reads and post-probe media limits.
- Silent videos persist an empty transcript instead of failing ASR.
- Partial and failed embedding-index commands now return a non-zero exit status.
