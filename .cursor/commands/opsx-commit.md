---
description: "Atomic local Git commits for product/code/OpenSpec/docs. Git packaging only — not a verification gate. Process surfaces redirect to /opsx-meta-commit."
argument-hint: "GO [optional change-id / context / ALL]"
---

# STROY-SYSTEM TARGET OVERRIDE - HIGHEST PRIORITY

This command was transferred from a project-specific OPSX realization.
For stroy-system, product-specific realization MUST resolve through:

  opsx/project/capabilities.yaml
  opsx/shared/target-capability-resolution.md

Any inherited donor literal (WB Sky, adv-bot, Poetry, pyproject.toml, app/**,
tests/**, npm/package.json, src/**, public/**, deploy names) is historical or
compatibility text only and is NOT execution authority.

Before any product-specific read/write/command execution, resolve the
corresponding TARGET capability. If unresolved, fail closed:

STATUS: FAIL_CLOSED
REASON: TARGET_CAPABILITY_UNRESOLVED
CAPABILITY: <required capability>
NEXT_ACTION: define/discover capability in opsx/project/capabilities.yaml

Do not infer product stack from the donor repository.
Control-plane runtime paths under tools/temporal and tools/cursor_executor are
separate from product Temporal/runtime adoption.

# /opsx-commit

**Role:** Git packaging — classify dirty paths, stage exact paths, create atomic
local commits, return commit SHAs. This command does **not** own product quality
verification.

## Architectural invariant (mandatory)

```text
Commit creation MUST NOT be blocked by absence, staleness, or failure of
product verification.

Allowed: commit when verification=PENDING | FAIL | ABSENT.
Forbidden: run or require static-check, tests, build, browser/security/scenario
checks; requiring verification ledger COMPLETE; or verify/archive PASS as a commit
precondition.
```

Quality gates belong to the verification/integration contour (`/opsx-static-check`,
`/opsx-verify`, `/opsx-archive`, merge/land/release). They are **not** replayed
here.

## Authorization

Create local git commits **only** when user input contains the exact token `GO`.

**Precedence:** stable OPSX command contract (this file, `/opsx-supervisor` §19/§27,
semantic-write hard stops) **outranks** any `MINIMAL_AUTHORITY_GRANT_V0` object.
A grant may narrow scope; it MUST NOT authorize `git push` or override denylist.
See `.cursor/pipeline/contracts/EXECUTION_AUTHORITY_GRANT_V0.md` (`LIVE_AUTHORITY: NO`
until a later hook slice).

If `GO` is absent:

- do not modify files;
- do not stage files;
- do not commit;
- output only the proposed commit plan.

Optional scope tokens (case-sensitive when present):

| Token | Meaning |
|---|---|
| `ALL` | Commit all in-scope dirty paths per grouping rules (never `git add .` / `-A`) |
| `<change-id>` | Optional **filter**: limit commits to that change tree only |

### OpenSpec product scope under materialize / change-id (mandatory)

When **either**:

- the prompt contains `TEMPORAL_MATERIALIZE_CONTRACT`, or
- a resolved OpenSpec change-id is in `$ARGUMENTS` / preflight context, or
- any in-scope dirty path lies under `openspec/changes/<change-id>/`,

the **product lane** MUST treat the whole change tree as in-scope product
OpenSpec artifacts — including **untracked** paths under
`openspec/changes/<change-id>/**` (for example
`apply-process-exception/PROCESS-GATE-DEFERRAL.json` from break-glass /
process-gate deferral). Classify and stage them in the `spec(openspec)` group
when `GO` is present (with `ALL` forwarded from `/opsx-commit-all ALL GO`).

Do **not** leave change-scoped OpenSpec dirs untracked while reporting product
lane success; list any intentionally skipped paths under
`groups not committed:` with reason.

## Commit-only git allowlist (mandatory)

This command may mutate git **only** to create local commits.

**Allowed mutating git:**

- `git add <exact-path>` for the current commit group
- `git commit` (authorized only with exact token `GO`)
- `git restore --staged <file>` only to unstage unsafe / out-of-scope paths

**Allowed read-only git:** `branch`, `status`, `diff`, `log`.

**Forbidden (non-exhaustive):**

- `git add .`, `git add -A`, broad `git add` without explicit paths
- any `git rebase` / `git merge` / `git cherry-pick` progression
- `git push`, force-push, remote updates
- `git reset --hard`, `git clean -fd`, destructive checkout
- deploy, migrations, secrets access

## Verification boundary (mandatory)

This command **must not**:

- run `/opsx-static-check`, pytest, mypy, pyright, build, npm, browser,
  security review, scenario coverage, or any quality gate;
- requiring static-check FULL/FAST receipt or command-bus receipt;
- blocking commit because verification evidence is missing, stale, or failed;
- requiring verification ledger COMPLETE or verify/archive PASS.

If Owner pasted verification results, they may appear **only** as optional
commit-body notes supplied by Owner — never as executable gates.

## Content-edit ban (mandatory)

This command may change the **git index / local commit history** only. It must
**not** rewrite working-tree content to fix product issues.

**Forbidden:** `Write` / `StrReplace` on `app/**`, `tests/**`, `openspec/**`,
configs, or any product file. Never clear conflict markers or invent resolution
semantics here.

Staging may include paths already dirty or already conflict-resolved before
this command started. Unmerged paths or conflict markers in commit-group files
→ **COMMIT STOP** (see **Unresolved conflicts**).

## Purpose

Analyze the working tree and create safe atomic local commits by logical zones
for **product/code/OpenSpec/docs** changes.

Process surfaces (commands, rules, skills, agents, static-check, mirrors,
`AGENTS.md`) are **out of scope** → `/opsx-meta-commit`.

**LangGraph OPSX runtime** (`opsx/opsx_runtime/**` — agents, workflows, harness
tests colocated there) is **in scope for this command**, not `/opsx-meta-commit`.

**Source resolution:** Follow `.cursor/commands/opsx-source-resolution.md`. For
each change-id in the commit plan (filter token or discovered dirty trees),
read that change via canonical paths only — never inventory all of
`openspec/changes/`.

## Process-surface exclusion (mandatory)

Never stage or commit:

```text
.cursor/commands/**
.cursor/rules/**
.cursor/skills/**
.cursor/agents/**
.cursor/static-check/**
.cursor/pipeline/**
.cursor/opsx-review-commands
.agents/**
.github/skills/**
.github/prompts/**
.github/copilot-instructions.md
.github/git-commit-instructions.md
AGENTS.md
opsx/**
  (entire tree **except** `opsx/opsx_runtime/**` — see runtime lane below)
```

Paths under `opsx/**` (excluding the runtime lane) are process/control-plane
packaging (see `opsx/sources.yaml` allowlist); **never** stage them here — use
`/opsx-meta-commit` (`ALL GO` for full in-scope process tree).

### LangGraph runtime lane (in scope here)

Stage and commit when dirty:

```text
opsx/opsx_runtime/**
```

Group as `opsx-runtime` (separate from product `src/stroy/**` and OpenSpec
product trees). Typical subjects: `fix(opsx-runtime): …`, `test(opsx-runtime): …`.

Never stage under this lane:

```text
opsx/opsx_runtime/**/pytest-of-*/
opsx/opsx_runtime/.pytest_cache/**
```

(report as junk / restore; do not commit ephemeral pytest dirs).

If any of these are dirty:

```text
OUT_OF_SCOPE_REDIRECT:
NEXT_ACTION: /opsx-meta-commit ALL GO
```

If the dirty tree is **only** process surfaces:

```text
STATUS: OUT_OF_SCOPE_REDIRECT
NEXT_ACTION: /opsx-meta-commit ALL GO
```

## Mandatory preflight

Follow `.cursor/commands/opsx-execution-preflight.md`.

```bash
git branch --show-current
git status --short
git diff --stat
git diff --name-status
git diff --cached --stat
git diff --cached --name-status
```

Do not run `git fetch` unless remote state becomes required.

### Unresolved conflicts hard gate

**COMMIT STOP** before any `git add` / `git commit` when:

1. Unmerged index state (`UU`, `AA`, `DD`, `AU`, `UA`, `DU`, `UD`) or
   `Unmerged paths` in status.
2. Conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) in files to be staged.

```text
STATUS: COMMIT_STOP
WHY: unresolved_conflicts
NEXT_ACTION: resolve conflicts outside /opsx-commit, then rerun /opsx-commit GO
```

### Protected branch gate

Block when branch is `main`, `master`, `prod`, or `production` unless input
contains exact token `GO_ON_PROTECTED_BRANCH`. Never push.

### Sequencer / rebase in progress

If rebase/merge/cherry-pick is in progress:

- unresolved-conflicts gate still applies first;
- with `GO`, authorized local commit(s) are allowed when conflicts are clear;
- **hard stop** after commits — never advance the sequencer;
- report `REBASE_OR_SEQUENCER_IN_PROGRESS_LEFT_TO_OWNER: yes`.

## Detect OpenSpec change

Discover every `openspec/changes/<change-id>/` with in-scope dirty paths
(tracked or untracked). If Owner passed `<change-id>` in `$ARGUMENTS`, **limit**
classification to that id only.

When **multiple** change-ids are dirty and no filter token is present, **do not
block** — plan and (on `GO`) commit **one atomic group per change-id** (at
minimum separate `spec(openspec)` commits), in stable change-id sort order.
Never `STATUS: BLOCKED` solely because change-id count exceeds one; never require
Owner to rerun once per change-id unless they passed a narrow filter.

Optional bounded read per change-id in the plan: `proposal.md`, `design.md`,
`tasks.md` when useful for commit messages — do not inventory all of
`openspec/changes/`.

## Classification zones

Classify every in-scope changed file:

```text
1. openspec-specs
2. db-models-migrations
3. domain-business-logic
4. bot-ui-callbacks-messages
5. async-jobs-workers
6. tests
7. docs
8. ci-infra
9. opsx-runtime (`opsx/opsx_runtime/**`, LangGraph control plane)
10. unrelated-or-suspicious
```

Never classify process-surface paths here — redirect only.

## Automatic commit grouping

Preferred order:

```text
1. spec(openspec)
2. db/models/migrations
3. domain/business logic
4. bot UI/callbacks/messages
5. async jobs/workers
6. tests
7. docs
8. opsx-runtime (`opsx/opsx_runtime/**`)
9. chore/cleanup
```

Do not include `unrelated-or-suspicious` or process-surface files. Leave
suspicious files unstaged and report them.

## Staging rules (mandatory)

- **Exact-path staging only** — never `git add .` or `git add -A`.
- Prefer whole-file staging when the file belongs to one group.
- Mixed-zone file → `git add -p <file>`; if impractical, skip and report
  `requires manual git add -p`.
- Before each commit: `git diff --cached --stat`, `--name-status`, full cached diff.
- Unrelated staged changes → `git restore --staged <file>` and **COMMIT STOP**
  for that group.

## Commit message format

Subject (English + Russian parenthetical):

```text
type(scope): English summary (русское пояснение)
```

Types: `spec`, `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`, `perf`.

Body sections: What, Why, How, OpenSpec (change-id), Risk.

Optional **Verification** subsection: only Owner-supplied evidence or
`not evaluated by /opsx-commit` — never claim checks were run by this command.

## Execution mode

**Preview (`GO` absent):**

```text
Commit plan:
- proposed commits
- files per commit (exact paths)
- suspicious / unstaged files
- process-surface redirect list
```

**Execute (`GO` present):** for each group:

1. Stage exact paths.
2. Inspect staged diff.
3. Commit with detailed message.
4. Record commit SHA.
5. Continue to next group.

On git-safety blockers → **COMMIT STOP**; do not process later groups unless
earlier groups already committed (report `PARTIAL`).

## Final report

```text
STATUS: COMMITTED | PARTIAL | COMMIT_STOP | BLOCKED | OUT_OF_SCOPE_REDIRECT

Done:
- commits created: <sha> <subject> (paths…)

Remaining uncommitted:
- <paths>

Skipped:
- requires manual git add -p:
- suspicious/unrelated:

OUT_OF_SCOPE_REDIRECT:
- process files → /opsx-meta-commit ALL GO

REBASE_OR_SEQUENCER_IN_PROGRESS_LEFT_TO_OWNER: yes | no

NEXT_ACTION:
- verification (optional, separate contour): /opsx-static-check | /opsx-verify
- PR (optional): /opsx-create-pr
- process commit: /opsx-meta-commit ALL GO
```

PR creation and verification are **optional follow-ups**, not prerequisites.

## Absolute prohibitions

Never run inside `/opsx-commit`:

```bash
git push
git add .
git add -A
poetry run pytest
bash .cursor/static-check/bin/run.sh
/opsx-static-check
/opsx-verify
/opsx-fix-types
/opsx-test
npm run build
docker compose up
kubectl
alembic upgrade
alembic downgrade
```

Never heal-to-green. Never advance rebase/merge/cherry-pick sequencers.

---

## Target profile supplement (stroy-system)

A corrupted duplicate block (check modes / verification matrices) previously
appended after this section is **void**. Git packaging semantics in the canonical
body above are authoritative.

### Orchestrator tokens (log only)

`/opsx-commit-all` and similar forwards may pass `FULL_CHECK`, `FAST_CHECK`, or
`base=<ref>`. Parse them for the final report label `CHECK_MODE: not_applicable`
only. **Do not run** pytest, pyright, mypy, AI soft-chain, or antipattern gates;
**do not** COMMIT STOP on check outcomes, missing inferred tests, or unresolved
`base=`. Quality gates live under `/opsx-static-check`, `/opsx-verify`, and
`/opsx-archive`.

Commit bodies MUST use `Verification: not evaluated by /opsx-commit` unless Owner
 pasted external evidence in the same message.

### Multi change-id execute (mandatory)

On `GO`, when multiple change trees are dirty and Owner did not pass a
`<change-id>` filter, commit **all** in-scope groups in one run (serial commits,
stable change-id order). Preview mode lists every group; execute mode must not
stop with “rerun per change-id”.
