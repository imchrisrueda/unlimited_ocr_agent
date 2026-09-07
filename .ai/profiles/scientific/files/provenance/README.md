# Scientific provenance

Canonical RUN records answer: what data, version, code, commit, parameters, environment and agent produced each important computational result.

The mapping under `mappings/prov-o.json` uses PROV-O Entity, Activity and Agent concepts. RO-Crate 1.3 and Workflow/Provenance Run Crate profiles have independent version URIs and must be declared separately.

The metadata file under `templates/` is a template, not a valid final crate. Instantiate real metadata and validate it before claiming conformance.
