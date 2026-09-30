# ChessX implementation rules

This directory is the sole project repository. Never initialize or modify repositories outside it.
The frozen specification is docs/ChessX-Spec-v2.1.md. Rules are value-neutral; never add HP,
loyalty scores, dice winners, remote joins, neutral battle participants, or implicit object creation.

## Ownership and integration
- Domain/rules: strict models, pure geometry, legal mutations and invariants.
- LLM/contexts: provider capabilities, exposed-CoT validation, audience and prompt isolation.
- Runtime/storage/API: serialized durable steps, operation identity, pause/recovery, endpoints.
- Frontend: UI is a projection and never owns authoritative state or credentials.
Parallel agents edit only assigned directories. The root integrator owns cross-module contracts.
Do not commit other agents' unfinished work; root runs integration checks and creates commits.

## Secrets
Never print credential values from .env.local, Authorization headers, live response bodies, private runtime
logs or databases into tool outputs. Never send credentials to browsers or source control.
Only .env.example with empty credentials is tracked. Load keys server-side; use sanitized errors.
Server/secret-guard code may compare keys entirely in memory. Agents must not inspect raw environment files.
.data/logs/exports are local and ignored. Public errors must contain codes, not upstream responses.
Do not add remotes, push, or create GitHub resources without explicit authorization.

## Quality gates
Use uv run pytest, uv run ruff check ., uv run ruff format --check .,
and pnpm --dir frontend check/build/test. Run scripts/check_secrets.py before committing.
Persist cursor and state at every event commit. LLM wait never holds a DB transaction.
Fixture mode is explicitly labeled engineering fixture, never described as live AI.
Record real-provider verification as pending unless actual authorized calls succeed.
