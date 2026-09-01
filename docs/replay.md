# Selective policy replay

`ReplayPlanner` compares two published versions of one Policy. Changes limited to aggregation,
severity, enablement, or boolean expressions use `REEVALUATE`. Added, removed, or semantically
modified Requirements use `REINVESTIGATE`. Missing source decisions and open Appeals block
automatic execution.

The preview is read-only and produces a canonical SHA-256. Execution recalculates it and rejects
stale requests. A ReplayItem checkpoints each source Case. Unchanged Requirements receive
derived Evidence with `origin_evidence_id`; reevaluation creates no AgentRun, ToolRun, or
ModelCall. Changed Requirements become the explicit scope of the Stage 4 investigation handler.

`keep` preserves compatible visual Evidence. `invalidate-visual` records invalidation lineage and
adds visual Requirements to the investigation scope. Replay reuses media, segments, artifacts,
transcripts, OCR, captions, and search documents in either mode.

```bash
evistream replay-preview <policy-id> --from-version 1 --to-version 2
evistream replay-run <policy-id> --from-version 1 --to-version 2 --preview-hash <sha256>
evistream replay-status <job-id>
evistream replay-diff <job-id>
```
