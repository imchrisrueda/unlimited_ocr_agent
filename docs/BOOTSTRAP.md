# Bootstrap

## Required

- Git.
- CPython 3.11 or newer for `scripts/projectctl.py`.

No application language, database, cloud or LLM provider is required by the methodology.

## Codex

Install and authenticate Codex using the current official OpenAI documentation. Authentication is machine-local and must not be committed.

## Gemini CLI

Install and authenticate Gemini CLI using Google's current documentation. `.gemini/settings.json` loads `AGENTS.md`; verify with Gemini's `/memory show` command. Do not commit tokens.

## Optional tools

- Graphify: see `.ai/integrations/GRAPHIFY.md`.
- Cognee: see `.ai/integrations/COGNEE.md`.
- Local LLM: choose a provider per project and document privacy, hardware, model version and replacement strategy. No local model is installed automatically.

Bootstrap never installs optional tools or starts network services.
