---
name: agent-cli-design
description: Design or review polished, user-friendly CLIs for humans and agents, especially provider-backed resources, authentication, immutable versions, JSON automation, and asynchronous jobs.
---

# Agent-facing CLI design

Design one command contract that is comfortable for a human and deterministic for an agent. The CLI owns the product vocabulary, domain semantics, and orchestration. Provider CLIs, APIs, protocols, and resource layouts are implementation details behind adapters unless the user must act on them.

## Workflow

1. Inspect the existing command help, platform APIs, identifiers, authentication modes, and job states. Record unsupported or undocumented behavior as an open decision.
2. List independent user jobs, then group commands under stable domain nouns. A separate command must correspond to a separate intent, not merely a different backend or payload type.
3. Split read-only discovery from mutation. Resolve friendly names and movable tags before execution; persist the exact version, digest, or snapshot used.
4. Define each command's inputs, resolution rules, side effects, output schema, progress behavior, failure states, and recovery command.
5. Test the human path and the non-interactive Agent/CI path. The design is complete only when both can finish without parsing prose.
6. Review the actual terminal experience at normal and narrow widths. Verify hierarchy, wording, progress, errors, and final handoff—not only command correctness.

## Product-facing experience

- Treat the CLI as the product surface, not a transcript of assembled backend tools. Use the product's domain language throughout the happy path.
- Hide provider names, authentication protocols, internal endpoints, subprocess output, and resource layouts by default. Reveal them only when the user must choose between them or diagnose a provider-specific failure.
- Present one coherent interaction with a clear hierarchy: current action, progress or status, outcome, and next useful action. Do not stream unrelated backend banners and prompts into the same interface.
- Use progressive disclosure. Keep the default concise; place verbose diagnostics behind `--verbose` or `--debug`, and save long outputs in the run or output directory.
- Make terminal output visually consistent: stable labels, restrained color and symbols, aligned values, bounded line lengths, and terminology that does not change between commands.
- Design errors at the user's level: state what operation failed, which user-visible object was affected, what remains safe, and how to recover. Preserve internal details in logs or debug output.
- Aesthetic improvements must not compromise automation, accessibility, or truthfulness. Color is supplemental, spinners stop in non-interactive mode, and every important state remains legible as plain text.

## Command surface

- Prefer a small command tree such as `resource register`, `resource inspect`, and `eval submit` over unrelated top-level verbs.
- Keep one primary happy path. Put provider selection, output format, waiting, and context overrides in consistent flags.
- Use explicit flags when a positional value could mean either a remote path, local path, name, version, or output directory.
- A thin wrapper is justified when it adds domain resolution, a unified contract, or cross-provider behavior. Otherwise expose or call the provider command directly.
- Keep examples short. Command help is the source of truth; design documents retain decisions and unresolved questions rather than copying the full help text.

## Machine contract

- Offer machine-readable output consistently, preferably `-o json` or `--json`.
- Write the final machine result to stdout. Write progress, diagnostics, and logs to stderr. Human-mode decoration must never contaminate JSON output.
- Return human-readable names beside opaque IDs. Include the immutable reference used by every mutating command.
- Keep output schemas stable. Use nonzero exit codes and structured errors that name the failed object and the next useful command.
- Never print credentials. Read Agent/CI secrets from environment variables or delegated credential stores.

## Discovery and versions

Use a two-phase pattern:

1. **Discover** with read-only commands and JSON output.
2. **Execute** with the selected exact IDs and versions.

Names and tags are acceptable user inputs, but execution provenance must record immutable identities. When selecting a default version, choose the latest usable state required by the operation, not merely the numerically latest record.

## Authentication and context

- Provide one product-level interactive login for humans and a non-interactive token path for agents and CI.
- Provide `whoami` or `auth status` so callers can verify identity before mutation.
- Keep provider sessions independently verifiable internally, even when one upstream authorization can establish several sessions. Do not turn internal session boundaries into extra user steps without necessity.
- Show only the effective context needed for the user's decision. Put provider, endpoint, and protocol details in inspect or debug output unless recovery requires them.
- Allow explicit context overrides for reproducible automation.

## Long-running work

- Submission returns a stable run or job ID immediately.
- Provide status/get commands. Add `--wait` when bounded waiting is useful; keep fire-and-forget when the natural boundary is much longer.
- Define terminal states, partial results, retry identity, and idempotency before implementation.
- Progress reports logical work completed, not only whether a process is alive. Human mode should show a stable progress region with concise success/failure counts when available.
- End with a compact outcome and a path or command for detailed results; do not dump long machine-readable results into the terminal.

## Provider-backed resources

- Route resources by declared type and capability. Let the target platform perform platform-owned payload validation unless an agreed product contract requires earlier checks.
- Normalize provider results without leaking provider-specific structure into the normal user contract. Preserve original references in provenance and diagnostic output.
- Keep resource type, packaging form, and storage backend as separate concepts. A Dataset may be represented by a Bundle when the platform contract permits it.
- Keep external references such as model IDs explicit in internal provenance rather than copying artifacts across providers.

## Review gate

Before accepting a CLI design, verify that:

- every independent user job has one obvious entry point;
- the same operation has one product-level contract across providers;
- the happy path exposes only user-actionable domain concepts;
- output has one coherent visual hierarchy rather than stitched backend output;
- names, status, progress, errors, next actions, and result locations are immediately understandable;
- normal and narrow terminal widths remain readable, with and without color;
- every mutation records exact input versions;
- JSON automation requires no prose parsing or human-mode decoration;
- stdout and stderr have distinct roles;
- asynchronous work has an observable lifecycle;
- authentication supports both humans and agents without unnecessary repeated authorization;
- unknown provider behavior remains marked for confirmation;
- concise examples expose the primary path without burying it.

## Origin

These patterns were distilled from the Trisol “CLI 与 Agent Skill” design and Flywheel implementation feedback: CLI/API-first execution, thin orchestration, JSON-first discovery, exact version resolution, unified product-level authentication, explicit long-running job boundaries, and a terminal experience that presents Flywheel rather than its internal platform composition.
