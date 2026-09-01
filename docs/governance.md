# Case governance

Stage 5 separates investigation advice from formal moderation decisions. The Agent appends
Evidence. `RequirementAggregator` filters provenance and quality, creates an immutable
RequirementResult, and updates the Requirement pointer in one transaction. `RuleEvaluator`
then consumes the compiled policy with three-valued logic. Agent provisional verdicts are
stored only as Decision metadata.

The four aggregation states are `SATISFIED`, `NOT_SATISFIED`, `CONFLICTED`, and `UNKNOWN`.
Only Evidence with valid Case ownership, media time, modality, Artifact, ToolRun and ModelCall
provenance contributes to thresholds. The aggregation input hash includes the Requirement
semantic hash, compiler-owned configuration, Evidence summaries and source status.

Machine, review, and appeal decisions append to the same Case history. `current_result_id` and
`current_decision_id` are mutable pointers; the referenced audit rows are immutable. A Review
creates a Human Decision that supersedes the prior Decision. An Appeal can challenge only the
current Decision and can be resolved once.

Commands:

```bash
evistream case-evaluate <case-id>
evistream case-timeline <case-id>
evistream case-review <case-id> --reviewer <name> --verdict APPROVE --note <text>
evistream appeal-submit <case-id> --submitter <name> --statement <text>
evistream appeal-resolve <appeal-id> --reviewer <name> --verdict REJECT --note <text>
```
