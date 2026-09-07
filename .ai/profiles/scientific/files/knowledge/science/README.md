# Scientific knowledge layer

These JSON files are canonical, versioned scientific records. Each uses `{ "schema_version": 1, "items": [] }`. IDs are persistent and registered in `id_registry.json`; deleted IDs become tombstones.

Core traceability:

```text
Objective -> ResearchQuestion -> Hypothesis -> Experiment/Analysis
Dataset + Method + Run -> Result
Result -> Evidence -> Claim -> Conclusion
```

Evidence records whether a Result supports or contradicts a Claim. Insights start as inferred and unvalidated; they may create a hypothesis, analysis or TASK, never a Result or Conclusion directly.

Run `python scripts/projectctl.py science lint` before a reasoned scientific audit.
