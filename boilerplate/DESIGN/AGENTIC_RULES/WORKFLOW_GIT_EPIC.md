# Git Workflow Epic / GitHub Project

These rules complement `{AGENTIC_RULES_DIR}/WORKFLOW_GIT.md` for piloting an
EPIC tracked in a GitHub Project v2.

This file is a generic template for projects created from the starter-kit. A
project may add domain-specific fields, but it must not replace the core
traceability model: one EPIC, explicit child issues, linked PRs, reviewed
status transitions, and a release path that can be audited from GitHub.

## Principle

The Project is used to pilot execution. It must not become an unreadable audit
matrix.

- `Status` describes only the workflow state.
- `Workstream` or `Lot` describes the functional or technical area.
- `Risk` describes the nature of the technical risk.
- `Priority` describes processing order.
- Domain-specific fields are allowed only when their meaning is documented.

Do not use `Status` to classify a topic by theme, severity, lot, risk,
customer, component or priority. That information belongs in dedicated fields.

## EPIC structure

Every EPIC must have a visible decomposition.

Required structure:
- one main EPIC issue carrying the goal, scope, out-of-scope items, success
  criteria and release target;
- one or more child issues carrying concrete work items, risks, bugs, proofs or
  documentation tasks;
- one or more PRs linked to child issues when implementation or documentation
  changes are required;
- a Project view where the EPIC, child issues and active PRs can be followed
  without reading local notes.

The EPIC issue is the scoping object. It must not carry detailed execution that
belongs to child issues or PRs.

Child issues carry the problem, expected outcome and acceptance criteria. PRs
carry the implementation, review discussion and validation evidence.

Rules:
- do not track an EPIC only through a checklist in the EPIC body;
- do not open an implementation PR without a linked issue, except for an
  explicitly documented emergency hotfix;
- do not close the EPIC until all required child issues are closed or explicitly
  descoped in the EPIC;
- keep a short EPIC body section or comment listing active child issues and
  release PRs so progress is readable from GitHub;
- if a child issue is split, cross-link the replacement issues and update the
  EPIC trace.

Recommended EPIC tracking block:

```text
## Tracking

- Scope issue: #<epic>
- Child issues: #<n>, #<n>, #<n>
- Active PRs: #<pr>, #<pr>
- Release PR: #<pr or pending>
- Release target: <version, milestone, date or "none">
```

## Piloting scope

A project may temporarily track a component EPIC in a broader program Project.
This is acceptable only when the boundary is explicit.

Rules:
- identify which Project is the execution board for the EPIC;
- identify which repository owns the code and releases;
- do not mix unrelated program tasks into the EPIC without a classification
  field;
- if internal priorities, risks or arbitrations are tracked, keep the Project
  private and publish only factual implementation information;
- when the EPIC stabilizes into a long-lived product or component, consider a
  dedicated Project or view.

## Language of artifacts

Each project must define the language of public artifacts.

Default rule:
- public issues, PR titles, PR bodies, reviews, changelogs, release notes and
  documentation use the project's public language;
- internal Project comments may use the team's working language if they are not
  copied as-is into public repositories;
- information copied from private piloting into public issues or PRs must be
  rewritten as factual, implementation-useful content.

## Adoption in the generated repository

Each repository generated from the starter-kit should carry its own adapted
workflow rules.

Rules:
- keep these files under a stable rules or design directory chosen by the
  project;
- adapt placeholders and domain-specific fields through an issue, branch and PR;
- include an independent review when adopting or changing workflow rules;
- do not block urgent fixes only because the local rules adoption is not yet
  complete.

## Release train / RC flow

Use this flow when a release can impact multiple consumers, environments or
automation users. For small single-consumer repositories, the project may use
the nominal `main` PR flow from `{AGENTIC_RULES_DIR}/WORKFLOW_GIT.md` if the
EPIC explicitly says there is no RC train.

Rules:
- use a dedicated RC branch in the `rc/vX.Y.Z` format as the target of feature
  PRs belonging to the release train;
- treat `rc/vX.Y.Z` as an integration branch, not as a tag;
- do not use a branch name like `vX.Y.Z-rc`, too close to a SemVer prerelease
  tag;
- do not open release-train feature PRs directly against `main`;
- do not create a tag, GitHub release or publication from the RC branch;
- use `Refs #N` in feature PRs targeting the RC branch;
- reserve `Closes #N`, `Fixes #N` and `Resolves #N` for the RC -> `main` PR,
  except for an explicitly approved direct hotfix to `main`;
- forbid GitHub closing keywords in commit messages of feature PRs targeting
  the RC branch;
- move an issue whose fix is merged into the RC but not yet released to
  `Awaiting release`;
- open an RC -> `main` PR on GitHub when the train is ready;
- add an intermediate RC validation step between the last merge into the RC
  branch and the merge into `main`;
- the RC -> `main` PR carries release notes, upgrade risks and the aggregated
  `Closes #N` list for all included issues;
- the RC -> `main` merge is a human decision: a human reviewer must validate and
  approve it on GitHub;
- `main` must be the default branch and the RC branch must never become the
  default branch;
- `main` must be protected according to the project's risk level;
- release tags must be created only from commits reachable from `main`.

The RC -> `main` PR is the reliable source for closing issues. Do not rely on
feature -> RC PR bodies or commit messages to close issues.

For the RC -> `main` PR, `Rebase and merge` is forbidden unless the project has
documented an equivalent mechanism that preserves the aggregated closing
keywords in GitHub. Prefer `Squash and merge` or `Create a merge commit`, with
the aggregated `Closes #N` list in the PR body.

The RC validation step must be materialized in the RC -> `main` PR by a GitHub
comment containing:
- the canonical marker `RC-VALIDATION: OK <branch> <commit-sha>`;
- the list of executed checks;
- the list of aggregated issues;
- the upgrade or deployment risks;
- any accepted residual risk.

Without this comment, the RC -> `main` PR cannot be merged.

If the RC branch receives a new commit after the `RC-VALIDATION: OK <branch>
<commit-sha>` marker is published, that marker is invalidated and RC validation
must be replayed on the new SHA.

Minimal RC validation before merge into `main`:
- full CI green;
- project-specific regression checks re-run on the RC state;
- explicit verification of any high-impact risk field used by the project;
- release notes and upgrade notes in the project's public language;
- aggregated and deduplicated list of included issues, with `Closes #N` in the
  body of the RC -> `main` PR for each issue to close;
- confirmation that no still-open fix must go back to the RC.

Issue aggregation before the RC -> `main` PR:
1. run a dedicated aggregation script, or an equivalent documented command;
2. list all feature PRs merged into the RC branch since its creation;
3. collect `Refs #N`, `Closes #N`, `Fixes #N` and `Related to #N` from their
   bodies;
4. deduplicate issues;
5. verify that each issue belongs to the RC train scope;
6. report the intended closes in the RC -> `main` PR body with `Closes #N`;
7. verify that the PR body matches the aggregation output exactly;
8. after the RC -> `main` merge, verify each issue on GitHub and immediately
   fix any issue that stayed open by mistake.

After the RC -> `main` merge, delete the RC branch and never reuse the same
branch name for another delivery train.

## Direct hotfix to `main`

A direct hotfix to `main` is a strict exception to the RC flow.

Conditions:
- security incident, blocking production regression, data-loss risk, or
  equivalent urgent operational risk;
- public or private tracking issue, depending on sensitivity;
- PR directly to `main`;
- GitHub comment `HOTFIX-APPROVED: <issue>` posted by an authorized human before
  merge;
- mandatory human review;
- merge on GitHub only, by an authorized human by default, or by an automation
  agent only under explicit per-action human authorization.

After the hotfix merge:
- create the tag or release only from the hotfix commit present in `main`, if an
  immediate release is required;
- port the hotfix into any active RC branch through a dedicated PR;
- replay RC validation on the new RC branch SHA;
- update RC release notes.

## Project statuses

### Blocking finding

A finding is blocking when its content implies that a PR must not be merged
without a fix.

Blocking examples:
- uncontrolled API or contract drift;
- functional regression;
- violated persistence, security or authorization invariant;
- unrequested destructive change;
- secret, sensitive data or transient state persisted where it should not be;
- required test or check broken by the PR;
- incomplete issue/PR mapping when it prevents traceability.

Non-blocking examples:
- renaming, style, readability or refactor without behavioral impact;
- future improvement;
- desirable but not merge-required test or documentation;
- informative remark about an already-accepted residual risk.

Content prevails over format. An unmarked comment is still blocking if its
content requires a fix before merge. The canonical marker
`Verdict: REQUEST-CHANGES (comment)` serves readability; it is not the condition
that creates the blocking.

### Canonical markers

The following markers are canonical for GitHub comments related to piloting:
- `Verdict: REQUEST-CHANGES (comment)`: blocking review published as a comment;
- `READY FOR RE-REVIEW`: fix pushed and ready to be re-reviewed;
- `Project-move-request: <item> -> <status>`: Project move request when the
  reviewer lacks the rights;
- `Awaiting independent reviewer`: PR in `Review`, awaiting validation by
  another model or a human;
- `RC-VALIDATION: OK <branch> <commit-sha>`: RC validation completed on the
  indicated branch and commit;
- `HOTFIX-APPROVED: <issue>`: direct hotfix approved by an authorized human.

### `Plan`

Strict and limited use.

Put in `Plan` only:
- the main EPIC;
- an active scoping item;
- a governance decision under definition.

The main EPIC stays in `Plan` while its child issues or PRs move to `Todo`,
`In Progress`, `Review`, `Blocked`, `Awaiting release` or `Done`. Do not move
the EPIC by contagion from a child item.

Do not put in `Plan`:
- a known bug not started;
- an issue without an active scoping role;
- a backlog task;
- a private draft that is not the active scoping object.

A Project whose `Plan` column contains the backlog is considered badly piloted.

### `Todo`

Put in `Todo` any identified but not-started work:
- open bug without active implementation;
- issue to handle later;
- inactive governance task;
- private requalification item that is not in progress.

### `Draft`

Put in `Draft` only:
- a GitHub draft PR that is not in active correction;
- a deliverable being written that already exists as a concrete draft.

Do not use `Draft` to mean "to think about".

If a draft PR is actively worked on, or if a review requested a blocking fix,
its Project status is `In Progress`.

### `In Progress`

Put in `In Progress` when work has actually started:
- assigned and picked-up issue;
- active work branch;
- PR, including draft, in active correction or execution;
- execution in progress on the team side.

An issue can stay `In Progress` while its PR is in `Review`.

A PR returns to `In Progress` as soon as a review publishes a blocking finding,
a mandatory fix before merge, or an equivalent request-changes.

Do not use `Blocked` for a fix requested by review: as long as the team can push
a fix, the correct state is `In Progress`.

### `Review`

Reserve `Review` for PRs ready to re-read, or a deliverable whose only
remaining activity is review.

An item stays in `Review` only if the feedback is non-blocking, or if the
reviewer concludes the PR can move forward without a mandatory fix.

An item can stay in `Review` while awaiting a mandatory independent validation.
In that case, post a GitHub comment with the marker
`Awaiting independent reviewer`. Do not use `Blocked` for this wait.

After fixing a blocking finding, the item returns to `Review` only when the
pilot model, the PR author, or the team explicitly indicates the PR is ready for
re-review. A simple push is not enough. Accepted signals:
- `READY FOR RE-REVIEW` comment;
- GitHub re-review request;
- explicit comment resolving each blocking finding.

If several models act through the same GitHub account, validation relies on the
separation of model roles. The re-review comment must indicate which model holds
the reviewer role. If the same model fixed and reviewed, a validation by another
model or a human is mandatory before merge.

An issue's status never automatically follows its PR's status. The issue
carries the problem; the PR carries the execution. By default, do not move an
issue to `Review` only because its PR is in review.

A source issue linked to a PR with a blocking finding stays `In Progress` by
its own logic, because the problem it carries is not solved, even if the PR
status evolves separately.

### `Blocked`

Put in `Blocked` when the item cannot move forward without an external event:
- PR stacked on another PR;
- upstream decision awaited;
- blocking dependency bug;
- external quota, access or tooling temporarily blocking.

The blocking reason must be explicit in the issue, the PR, or the governance
item.

### `Awaiting release`

Put in `Awaiting release` an issue whose fix is already merged into the RC
branch and for which no code work remains, but which is not yet released because
the RC -> `main` merge has not happened.

This status exists because feature PRs merge into `rc/vX.Y.Z` with `Refs #N`,
which does not close the issue. The issue therefore cannot be `Done`, yet it is
no longer `In Progress`.

Rules:
- move an issue to `Awaiting release` only once its fix is merged into the RC
  branch and verified;
- do not use `Awaiting release` for a partially fixed issue;
- the issue moves from `Awaiting release` to `Done` only when the RC -> `main`
  PR closes it;
- a PR item is never `Awaiting release`: once merged, a PR item goes to `Done`.

### `Done`

Put in `Done` when the tracked object is really finished:
- closed issue;
- merged PR or closed without follow-up;
- executed governance task.

After a blocking finding, do not shortcut `In Progress` -> `Done`. The PR must
have a trace of positive re-review or independent validation before merge. After
merge, the PR item moves to `Done`. Do not leave a merged PR item in `Review`.

## Issues and PRs

Every EPIC must be traceable through issues and PRs.

Rules:
- the EPIC issue describes the overall scope and success criteria;
- child issues describe problems, needs, risks, proof tasks or documentation
  work;
- implementation PRs must reference a child issue with `Refs #N`, `Closes #N`,
  `Fixes #N` or `Related to #N`, depending on the workflow;
- a PR that resolves an issue outside an RC flow must contain `Closes #N` in
  its body, as defined in `{AGENTIC_RULES_DIR}/WORKFLOW_GIT.md`;
- a feature PR targeting an RC branch uses `Refs #N`; the RC -> `main` PR
  carries the final `Closes #N`;
- the issue describes the problem and acceptance criteria;
- the PR describes the technical execution and validation evidence;
- the `Status` of the issue and of the PR can diverge;
- an EPIC is not considered readable if active PRs cannot be found from its
  child issues or Project view.

A PR is considered tracked by the EPIC if it meets at least one of these
criteria:
- its body references an EPIC child issue;
- it is explicitly listed in the EPIC scope, plan, tracking block or a comment;
- it implements a decision, proof or release task attached to the EPIC;
- it was requested for review within the EPIC.

For tracked PRs:
- add the PR item to the Project when it helps review, status, release or risk
  tracking;
- carry over `Workstream` or `Lot`, `Risk` and `Priority` from the issue it
  implements when those fields exist;
- keep review discussion in the PR once the PR exists;
- keep scope and acceptance discussion in the issue unless the PR changes the
  agreed scope.

## Piloting fields

### `Workstream` / `Lot`

`Workstream` or `Lot` classifies the work area. It is never a status.

Examples:
- `S0 Foundation`
- `S1 API`
- `S2 Security`
- `S3 Documentation`
- `S4 Release`
- `S5 Operations`

Projects should replace these examples with their own stable workstream list.

### `Risk`

`Risk` describes the technical nature of the problem, not its urgency.

Common values:
- `none`
- `scope-gap`
- `api-contract`
- `authorization`
- `data-integrity`
- `destructive`
- `migration`
- `transient-retry`
- `state-secret`
- `unknown`

Examples:
- endpoint contract incompatible with clients: `api-contract`;
- missing authorization check: `authorization`;
- action that can delete or overwrite data: `destructive`;
- secret stored in persisted state: `state-secret`;
- missing retry on a transient error: `transient-retry`.

### `Priority`

`Priority` describes processing order.

- `P0`: must be handled before considering the EPIC safe.
- `P1`: important, but can follow the P0s.
- `P2`: useful improvement or structuring debt.
- `P3`: low-urgency backlog.

### Domain-specific impact fields

If a project needs a private impact field, define it explicitly in this section
or in a project-specific appendix.

Rules:
- do not expose internal impact fields as public labels if they contain
  sensitive prioritization or customer signals;
- explain allowed values and examples;
- make clear whether the field affects merge, release or only prioritization;
- aggregate impact at child issue level when possible; the EPIC should usually
  summarize, not hide, child-level risk.

Example field:

```text
Production impact:
- RED: can break an existing production workflow without user action.
- AMBER: requires attention during deployment or upgrade.
- GREEN: no production impact identified.
```

## Owner

The Project `Owner` field represents piloting, not necessarily the native
GitHub assignment.

Rules:
- use the native GitHub assignment for the person who implements;
- use `Owner` for the group responsible for piloting;
- do not deliberately contradict an explicit GitHub assignee;
- if a divergence exists, document it.

## Public / private

The repository may be public while the piloting Project stays private.

Rules:
- do not publish anxious labels or internal prioritization signals;
- keep internal flags in the private Project;
- public issues must stay factual, reproducible and useful to implementation;
- public PRs must contain implementation and validation facts, not private
  arbitration;
- do not publish private risk labels, customer names, internal priorities or
  confidential operational comments unless explicitly approved.

## Mandatory reviews

Each important EPIC step must plan a separation between a pilot model and a
reviewer model.

The roles are not tied to a specific tool:
- one model can pilot while another reviews;
- the roles can be swapped from one step to the next;
- another model can hold one of the two roles if the context allows.

Rules:
- do not have the same model carry both the piloting decision and its review
  when a second model is available;
- explicitly note which model pilots and which reviews;
- the review must challenge the mapping, statuses, risks, priorities and
  Project impacts;
- any GitHub PR requested for review must receive a review or a GitHub comment,
  per `{AGENTIC_RULES_DIR}/WORKFLOW_GIT.md`;
- if the external reviewer cannot be called for confidentiality reasons, or if
  the tooling refuses the call even after user authorization, do not bypass;
- in that case, do a local review by the available model, note the exception,
  and continue only if the decision stays reversible or explicitly validated.

## Project modification discipline

Before modifying the Project:
1. Read the real GitHub state.
2. Produce an explicit issue / PR / Project mapping.
3. Have the reviewer model review, or document the authorized local exception
   if the external reviewer is unavailable.
4. Apply through an idempotent script or a documented manual sequence.
5. Verify the result by an independent GitHub read.
6. Update memory when the project uses Live Memory.

After each lot, verify that:
- the `Plan` column does not contain backlog items;
- every EPIC has child issues;
- every active implementation PR can be traced to an issue;
- every issue in `Awaiting release` is represented in the release PR closure
  list.

## GitHub Project execution discipline

GitHub Project modifications consume GraphQL quota and can fail mid-lot.

Rules:
- check the GraphQL quota before Project mutations;
- do not launch a series of mutations if the remaining quota is too low;
- if a mutation fails mid-lot, read the exact state before retrying;
- resume only the missing or incomplete items;
- do not conclude a lot on the sole output of a write script;
- verify by an independent read, targeted if needed to save quota;
- avoid partial updates that leave an inconsistent Project view.

## `Plan` column hygiene

After any Project operation, check the `Plan` column.

Rules:
- the EPIC may stay in `Plan`;
- truly active scoping items may stay in `Plan`;
- not-started bugs and tasks must be in `Todo`;
- inactive private drafts must be in `Todo`;
- if more than a few non-scoping items appear in `Plan`, fix it before adding a
  new lot.
