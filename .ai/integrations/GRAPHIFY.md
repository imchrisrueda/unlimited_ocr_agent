# Graphify integration

Status: optional, disabled by default. Last documentation check: 2026-08-25.

## Identity and purpose

This integration refers to:

- project: `Graphify-Labs/graphify`;
- Python package: `graphifyy`;
- installed command: `graphify`;
- official documentation: <https://graphify.com/docs>.

Graphify creates a local structural/semantic graph from code and supported documents. Use it for navigation, dependencies, call paths, architecture and impact analysis when the repository is large enough to benefit.

## Installation

Installation is explicit and machine-local. Current official options include:

```text
uv tool install graphifyy
graphify install
```

Do not run these commands from `projectctl`. Re-check the official documentation before installation because the integration is externally versioned.

## Build and rebuild

From a supported coding assistant, the documented skill command is:

```text
/graphify .
/graphify . --update
```

Typical derived outputs are:

```text
graphify-out/graph.html
graphify-out/GRAPH_REPORT.md
graphify-out/graph.json
```

`graphify-out/` is ignored by Git. Delete and rebuild it without affecting canonical project knowledge.

## MCP

Graphify can serve a generated graph over MCP using stdio or HTTP. Prefer stdio for one local client. If HTTP is genuinely required, bind safely and configure authentication according to the current official MCP documentation: <https://graphify.com/docs/mcp>.

Never commit API keys. Do not expose an unauthenticated graph server to an untrusted network.

## Planning and review

When enabled and fresh:

1. query affected components, imports, calls and tests;
2. distinguish extracted, inferred and ambiguous relations;
3. verify material findings against canonical source;
4. inspect the TASK and Git diff;
5. run tests;
6. use the graph to look for missed impact.

Graphify complements source, diff and tests. A stale or unavailable graph must never block a project whose canonical files remain valid.
