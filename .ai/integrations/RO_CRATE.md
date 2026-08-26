# RO-Crate and provenance

Status: SCIENTIFIC profile capability; no crate is generated automatically. Last documentation check: 2026-08-25.

## Versioned references

- RO-Crate Metadata Specification 1.3, Recommendation: <https://www.researchobject.org/ro-crate/specification/1.3/index.html>.
- PROV-O, W3C Recommendation: <https://www.w3.org/TR/prov-o/>.
- Workflow Run RO-Crate profile collection: <https://www.researchobject.org/workflow-run-crate/profiles/>.
- Provenance Run Crate 0.5: <https://www.researchobject.org/workflow-run-crate/profiles/provenance_run_crate/>.

RO-Crate and Workflow/Provenance Run Crate have independent versions. Declare each applicable profile URI in `conformsTo`; do not assume that choosing RO-Crate 1.3 silently upgrades another profile.

## Canonical model

The SCIENTIFIC profile maps:

- datasets, code, parameters, results and artifacts to `prov:Entity`;
- analyses and runs to `prov:Activity`;
- people, organizations and software agents to `prov:Agent`;
- inputs to `prov:used`;
- outputs to `prov:wasGeneratedBy`;
- derivations to `prov:wasDerivedFrom`;
- responsibility to `prov:wasAssociatedWith` or `prov:wasAttributedTo`.

The mapping is stored at `provenance/mappings/prov-o.json` after profile activation.

## Generation and validation

`provenance/templates/ro-crate-metadata.template.json` is deliberately incomplete and contains placeholders. It is not a valid final crate. A future generator may instantiate project metadata, files and RUN records, then validate the output with a version-compatible RO-Crate validator.

For complex computational workflows, consider Process Run Crate, Workflow Run Crate or Provenance Run Crate according to the granularity actually available. Do not require workflow profiles for a simple one-step analysis.

## Git and data policy

Version small provenance metadata and templates. Large data may remain external, but record URI/path, version, SHA-256, size, schema, source, acquisition date, license and access restrictions. Do not package restricted data into a crate without explicit authorization.
