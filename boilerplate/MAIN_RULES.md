# Project Rules

> **Read this file first, at the beginning of every session and every task.**
> This file is the authoritative entrypoint. It does not replace the detailed
> rules: read every file in `DESIGN/AGENTIC_RULES/` before starting work.

## Rule Index

| File | Scope |
| ---- | ----- |
| `MAIN_RULES.md` *(this file)* | Non-negotiable obligations and rule index |
| `DESIGN/AGENTIC_RULES/WORKSPACE_ADVANCE_RULES.md` | Persistent memory, Live Memory, Graph Memory, bank hygiene |
| `DESIGN/AGENTIC_RULES/WORKFLOW_ENGINEERING.md` | Engineering discipline, adversarial review, test rigor, human gates |
| `DESIGN/AGENTIC_RULES/WORKFLOW_GIT.md` | Git workflow, PR to main, issue links, PR review process |
| `DESIGN/AGENTIC_RULES/WORKFLOW_GIT_EPIC.md` | EPIC piloting, GitHub Project v2, statuses, fields, release traceability |

## Non-Negotiable Obligations

1. **Read the memory bank at startup** - load the project rules, the full bank,
   and recent live notes before doing any work. Configure the concrete
   `{LIVE_MCP_SERVER}`, `{SPACE}`, `{GRAPH_MCP_SERVER}` and `{GRAPH_MEMORY_ID}`
   values in `WORKSPACE_ADVANCE_RULES.md`.
2. **Use independent adversarial review** - run the project-defined reviewer at
   the beginning and end of non-trivial work, and before any GREEN application
   code commit. A RED test commit may precede the final review. A GREEN commit
   is allowed only with a favorable verdict and no blocker. PRs and merges need
   an explicit GO verdict.
3. **Get explicit human approval for outward actions** - GitHub mutations,
   Project changes, issue/PR comments, merges, releases, deployments, shared
   infrastructure actions and any irreversible side effect require an explicit,
   per-action human GO. Approval in one context does not carry to the next.
4. **Merge to `main` only through GitHub PRs** - never merge locally into
   `main`, and never commit directly on `main` unless the project owner has
   explicitly defined and approved an emergency exception.
5. **Keep exposed surfaces aligned** - when a feature is exposed to users or
   agents, keep the MCP tool surface, admin API/UI, CLI commands and interactive
   shell coherent where applicable.
6. **Apply the symmetric audience check** - for every human-facing operation,
   ask: "How does an MCP agent do the same thing?" For every agent-facing
   operation, ask: "How does a human inspect or administer it?"
7. **Write non-complacent tests** - tests must challenge errors, boundaries,
   authorization, failure modes and semantics. Never write tests that only prove
   the implementation can pass.
8. **Treat documentation as a deliverable** - update `README.md`,
   `CHANGELOG.md`, `DESIGN/` and operational docs when the change affects
   behavior, workflow, architecture or user expectations.
9. **Maintain regular memory notes** - write atomic live notes for durable
   facts, decisions, issues and completed milestones. Consolidate only after a
   synthesis note and explicit human validation.
10. **Keep EPICs traceable** - every EPIC must expose its child issues, active
    PRs, release PR and status in GitHub. Progress must be understandable from
    the Project and repository, not from private chat history alone.

## Communication

Use the project's working language. Be concise and didactic. Explain major
changes before making them. Ask when the risk is real or the project context is
ambiguous; do not invent project-specific values.
