# ADR 0001: Replay Evidence lineage

Status: accepted

Policy replay creates new Cases and audit records for the target Policy version. It does not move
or overwrite source records. Reusable Evidence becomes a derived row that points to
`origin_evidence_id`; reusable RequirementResults point to `origin_result_id`. `replay_lineage`
records reuse, invalidation, and recreation independently of current pointers.

This structure keeps every Decision tied to the exact Policy version and inputs that produced it.
It also permits deterministic replay recovery because stable derived IDs and ReplayItem
checkpoints prevent duplicate rows.
