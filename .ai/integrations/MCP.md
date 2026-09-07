# MCP integration policy

Model Context Protocol connections are optional adapters. They do not change the source-of-truth hierarchy.

Before enabling any MCP server, document:

- server identity and version;
- tools/resources exposed;
- stdio, loopback or remote transport;
- authentication and authorization;
- data read/write scope;
- data egress, logging and retention;
- failure behavior and removal procedure.

Prefer local stdio for single-machine derived indexes. Remote HTTP requires explicit network controls. Never commit secrets in MCP configuration; reference machine-local environment variables by name where the client supports it.

An unavailable MCP server must not break canonical validation. Agent findings obtained through MCP remain derived or interpretive until verified against canonical files.
