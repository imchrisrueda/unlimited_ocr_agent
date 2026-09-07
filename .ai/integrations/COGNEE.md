# Cognee integration

Status: optional, disabled by default. Last documentation check: 2026-08-25.

Official documentation: <https://docs.cognee.ai/> and <https://docs.cognee.ai/cognee-mcp/mcp-overview>.

## Purpose

Cognee can provide persistent semantic memory and retrieval across sessions or clients. It is most relevant to a SCIENTIFIC project whose canonical context has become large or heterogeneous. It is an index, not project truth or scientific evidence.

## Local rebuild mode

Default conceptual flow:

```text
canonical Git content -> Cognee processing -> machine-local index
```

Each machine may rebuild its own index. Rebuildability means the index can be recreated from canonical inputs; it does not promise byte-identical output when extraction uses nondeterministic models. Record Cognee, model, embedding provider and configuration versions when reproducibility matters.

## Shared API mode

Cognee MCP can connect multiple clients to a central Cognee backend. Use this only when shared memory provides material value. Require authentication, restrict hosts/origins, encrypt transport where appropriate and document retention/deletion.

The official local-setup documentation distinguishes standalone MCP from API mode: <https://docs.cognee.ai/cognee-mcp/mcp-local-setup>.

## Privacy boundary

“Local” is not one setting. Evaluate separately:

- where the graph/database is stored;
- whether MCP uses stdio, loopback HTTP or a remote endpoint;
- which LLM and embeddings provider processes content;
- what telemetry, logs and backups retain;
- which credentials are required.

A local database with a cloud LLM still sends content off-machine. Sensitive projects require providers and configuration that have been explicitly reviewed. Never store `.env`, API tokens or database credentials in Git.

## Ontology

The canonical scientific model remains in `.ai/schemas/` and `knowledge/science/`. Cognee may consume an RDF/OWL ontology derived from it. Official Cognee ontology support treats ontology files as optional reference vocabularies: <https://docs.cognee.ai/core-concepts/further-concepts/ontologies>.

## Failure and removal

If Cognee is absent, unavailable or deleted:

- TASK, source, tests, scientific records and provenance remain intact;
- `projectctl validate` and `science lint` continue working;
- only semantic retrieval/persistent index capability is lost.
