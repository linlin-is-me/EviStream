# Stage 2 verification

Stage 2 adds versioned moderation policies, cases, requirements, evidence, requirement
results, decisions and tool runs. The three demo policies compile into ordinary and
exception requirements without a model call.

## Verification

```bash
make dev-infra
alembic upgrade head
evistream seed-demo --check
make verify-stage2
```

The database gate covers an empty migration, the Stage 1 to Stage 2 upgrade, a Stage 2
downgrade and re-upgrade, policy publication, case creation and seed idempotency. CI uses
pgvector PostgreSQL and does not require an external model key.

Local WSL2 verification on 2026-09-01 completed with 55 tests passing and 88.94% Python
coverage. Ruff and strict mypy checks also passed.

## Demo metadata

`configs/demo/stage2-cases.yaml` contains nine metadata-only cases: one clear violation,
one explicit context exception and one insufficient-evidence case for each policy. It does
not insert placeholder media, evidence or decisions. Materialization requires an explicit
fixture-to-Video mapping whose Videos are already ready.

## Deferred work

Stage 3 implements retrieval and tools. Stage 5 implements evidence aggregation and the
rule evaluator. Stage 7 supplies the complete media and golden-case datasets.
