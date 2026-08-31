# Policy format

Stage 2 policy files are UTF-8 YAML documents under `configs/policies`. The loader rejects
files larger than 256 KiB, duplicate mapping keys and unknown fields.

Required fields are `id`, `version`, `name`, `enabled`, `severity`, `trigger_terms`,
`requirements`, `exceptions` and `decision`. Requirement types are `visual_presence`,
`temporal_context`, `speech_content` and `text_presence`.

Decision expressions support recursive non-empty `all` and `any` nodes. Reject conditions
may reference ordinary requirements. Escalation conditions may also reference compiled
exception requirements, `unresolved_exception` and `contradictory_evidence`.

```bash
evistream policy-validate configs/policies/violence-weapon-v1.yaml
evistream policy-compile configs/policies/violence-weapon-v1.yaml
evistream policy-publish configs/policies/violence-weapon-v1.yaml --lifecycle published
```

Compilation is deterministic. YAML whitespace and mapping order do not change the semantic
SHA-256. Published versions are immutable; a semantic change requires the next integer
version.
