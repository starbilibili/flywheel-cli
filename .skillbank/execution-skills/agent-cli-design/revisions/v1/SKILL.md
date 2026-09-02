---
name: agent-cli-design
description: Design or review CLIs used by humans and agents, especially provider-backed resource registries, authentication, immutable versions, JSON automation, and asynchronous jobs.
---

# Agent-facing CLI design

Design one command contract that is comfortable for a human and deterministic for an agent. The CLI owns domain semantics and orchestration; provider CLIs and APIs remain the execution truth.

## Workflow

1. Inspect the existing command help, platform APIs, identifiers, authentication modes, and job states. Record unsupported or undocumented behavior as an open decision.
2. List independent user jobs, then group commands under stable domain nouns. A separate command must correspond to a separate intent, not merely a different backend or payload type.
3. Split read-only discovery from mutation. Resolve friendly names and movable tags before execution; persist the exact version, digest, or snapshot used.
4. Define each command's inputs, resolution rules, side effects, output schema, progress behavior, failure states, and recovery command.
5. Test the human path and the non-interactive Agent/CI path. The design is complete only when both can finish without parsing prose.

## Command surface

- Prefer a small command tree such as `resource register`, `resource inspect`, and `eval submit` over unrelated top-level verbs.
- Keep one primary happy path. Put provider selection, output format, waiting, and context overrides in consistent flags.
- Use explicit flags when a positional value could mean either a remote path, local path, name, version, or output directory.
- A thin wrapper is justified when it adds domain resolution, a unified contract, or cross-provider behavior. Otherwise expose or call the provider command directly.
- Keep examples short. Command help is the source of truth; design documents retain decisions and unresolved questions rather than copying the full help text.

## Machine contract

- Offer machine-readable output consistently, preferably `-o json` or `--json`.
- Write the final result to stdout. Write progress, diagnostics, and logs to stderr.
- Return human-readable names beside opaque IDs. Include the immutable reference used by every mutating command.
- Keep output schemas stable. Use nonzero exit codes and structured errors that name the failed object and the next useful command.
- Never print credentials. Read Agent/CI secrets from environment variables or delegated credential stores.

## Discovery and versions

Use a two-phase pattern:

1. **Discover** with read-only commands and JSON output.
2. **Execute** with the selected exact IDs and versions.

Names and tags are acceptable user inputs, but execution provenance must record immutable identities. When selecting a default version, choose the latest usable state required by the operation, not merely the numerically latest record.

## Authentication and context

- Provide interactive login for humans and a non-interactive token path for agents and CI.
- Provide `whoami` or `auth status` so callers can verify identity before mutation.
- Treat provider sessions as separate even when providers share one upstream account.
- Make team, project, profile, and endpoint context visible. Allow explicit overrides for reproducible automation.

## Long-running work

- Submission returns a stable run or job ID immediately.
- Provide status/get commands. Add `--wait` when bounded waiting is useful; keep fire-and-forget when the natural boundary is much longer.
- Define terminal states, partial results, retry identity, and idempotency before implementation.
- Progress reports logical work completed, not only whether a process is alive.

## Provider-backed resources

- Route resources by declared type and capability. Let the target platform perform platform-owned payload validation unless an agreed Flywheel contract requires earlier checks.
- Normalize provider results without hiding the original reference. Preserve both a friendly tag and the immutable digest or version.
- Keep resource type, packaging form, and storage backend as separate concepts. A Dataset may be represented by a Bundle when the platform contract permits it.
- Keep external references such as Trisol model IDs explicit rather than copying model artifacts into another provider.

## Review gate

Before accepting a CLI design, verify that:

- every independent user job has one obvious entry point;
- the same operation has one contract across providers;
- every mutation records exact input versions;
- JSON automation requires no prose parsing;
- stdout and stderr have distinct roles;
- asynchronous work has an observable lifecycle;
- authentication supports both humans and agents;
- unknown provider behavior remains marked for confirmation;
- concise examples expose the primary path without burying it.

## Origin

These patterns were distilled from the Trisol “CLI 与 Agent Skill” design: CLI/API-first execution, thin Agent Skill orchestration, JSON-first discovery, exact version resolution, human and PAT authentication, context selection, and explicit long-running job boundaries.
