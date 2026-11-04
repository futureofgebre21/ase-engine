# ASE Architecture (Phase 1)

## 1. MVP: the one demo workflow
Import a small sample repo -> inspect -> grounded audit -> propose one fix -> show diff ->
approve -> apply -> run tests in a sandbox -> show verified results + run history ->
show that an unapproved push is blocked.

Everything else (create-new-project, deployment, many stacks) is out of MVP until this works.

## 2. User stories and acceptance criteria
| # | Story | Acceptance criteria |
|---|-------|---------------------|
| 1 | As a user I import a public GitHub repo by URL | Only `https://github.com/<owner>/<repo>` accepted; size/file limits enforced; escaping symlinks rejected; failure shows a clear error |
| 2 | I see what is in the repo | Inventory lists files, language, dependency files, tests, entry points; secrets/binaries/oversized files excluded from model context |
| 3 | I get an audit | Each finding has id, severity, category, evidence (file/line), fix, confidence, status; `confirmed` requires evidence; static facts and LLM opinions labelled separately |
| 4 | I review a plan and diff before anything changes | Plan lists files in scope + risks; writes limited to workspace and approved scope; diff shown first |
| 5 | I approve, ASE applies and verifies | Checks run in isolation; outcomes are passed/failed/skipped/unverified with real exit codes; repairs capped |
| 6 | I see run history | Every run: id, states, timestamps, actions, checks, limitations |
| 7 | External actions need approval | Push/deploy impossible without a recorded approval; result shown only if the API confirms it |

## 3. Key decisions
- **State machine first** (`core/states.py`): the agent cannot reach EXECUTING without passing
  AWAITING_APPROVAL. Tested.
- **Evidence-gated models** (`core/models.py`): a finding cannot be `confirmed` without evidence;
  a check cannot be `passed` without observed exit code 0. The type system blocks fake success.
- **Provider-neutral LLM adapter** (Phase 5) with a labelled mock; SDK code stays in adapters.
- **Typed actions, not shell strings**: the LLM proposes actions; a deterministic policy validates.
- **Storage**: JSON files per run to start; add a database (and Alembic) only if needed.
- **Execution**: disabled by default. Needs Docker (or equivalent) with no network, no host secrets,
  CPU/memory/time limits. If unavailable, ASE says so instead of faking it.

## 4. Layers (dependencies point downward only)
UI (Streamlit) -> application services -> agent / repository / execution / integrations
-> core (models, states, errors, config) + security.

## 5. Unverified items (must be checked before relying on them)
- Hackathon rules, eligibility, and judging criteria.
- LLM provider: availability from Ethiopia, pricing/free credits, rate limits, data policy.
- Hosting/deployment provider terms and regional availability.
- Docker availability on the developer machine.
- Exact installed dependency versions (lower bounds only in `pyproject.toml`).

## 6. Build order
Phase 3 UI shell -> 4 import/inventory -> 5 audit -> 6 plan/patch -> 7 sandbox -> 8 repair/history
-> 9 GitHub -> 10 hardening/demo.

## 7. Phase 3 notes
- UI code lives in `src/ase/ui/` (not `app/components/`) so imports work the same under
  `streamlit run`, pytest, and installed mode. `app/main.py` is a thin entrypoint.
- UI -> `ProjectService` -> `JsonRunStore`. The UI never touches storage or security code directly.
- Unavailable features are shown as explicit 'not available yet' states, never as dead success.

## 8. Phase 4 notes
- Import flow: validate URL -> clone to `<workspace>/repos/<run_id>` -> symlink scan -> inventory -> state IMPORTED. Any failure deletes the partial workspace and records the run as FAILED with a redacted reason.
- `repository/context.py` is the single approved way to read repo files for a model: blocks sensitive files, symlinks, binaries, redacts secrets, bounds size.
- Test fixture: `tests/fixtures/sample_repo` (intentionally buggy cart; a prompt-injection README and a fake secret for security tests). Excluded from pytest and Ruff.
- ANALYZED is reserved for the Phase 5 audit; the inventory alone ends at IMPORTED.

## 9. Phase 5 notes
- Flow: select context files (inventory-approved, redacted, size-bounded) -> build versioned prompt -> provider call (bounded transient retries, hard model-call budget) -> parse JSON with Pydantic (bounded re-ask on malformed output) -> validate evidence -> `AuditReport` -> run state ANALYZED.
- `Finding.status` stays `unverified` for AI findings. `confirmed` is reserved for deterministic confirmation (e.g. a failing test in Phase 7).
- `Evidence.location_verified` means ASE found the cited code; it says nothing about whether the model is right.
- Token usage and cost are recorded only when a provider reports them; otherwise they show as unknown.
- A failed audit leaves the run IMPORTED with an error action so it can be retried.
- Prompt version: `audit-v1` (`llm/prompts/audit.py`). Eval set: `tests/evals/test_audit_eval.py`.
- Not built on purpose: the real provider adapter (eligibility, pricing, rate limits and data policy are unverified for Ethiopia).

## 10. Pipeline contract (steps 1-6)
Steps 1-5 run automatically, only inside an isolated per-run workspace copy; nothing external
happens. Step 6 (push, deploy) always waits for an explicit user approval.
1. Analyze and import: clone, inventory, DevOps-file detection (deterministic facts).
2. Audit: AI review as senior software, DevOps and full-stack engineers; evidence-verified.
3. Architecture and plan (not built). 4. Build (not built; writes only inside the workspace).
5. Test and verify (not built; real sandbox runs; bounded repair loop, stops honestly).
6. Push and deploy (not built; success is shown only when GitHub or the host confirms it).
Each stage records its result on the run; a failed stage stops the pipeline.

## 11. Step 3 notes
- Input: verified audit findings (passed to the model as untrusted data in AUDIT_FINDINGS.json)
  plus the same inventory-approved, redacted files the audit saw.
- `agent/plan_validation.py` is the policy layer: the model proposes, deterministic code decides.
- Change ids (C-001...) are assigned by ASE. A plan with zero valid changes is an error.
- A failed plan leaves the run ANALYZED so it can be retried; success moves it to PLAN_READY.
- Prompt version: `plan-v1` (`llm/prompts/plan.py`).

## 12. Step 4 notes
- Two directories per run: `repos/<run>` (baseline, never written after clone) and
  `build/<run>` (working copy). Diff = baseline vs build; rollback = delete the build copy.
- State flow: PLAN_READY -> AWAITING_APPROVAL -> EXECUTING -> BUILT (code written, not verified)
  or FAILED (nothing could be applied). The approval is recorded with its scope.
- Modify = search/replace edits applied by code to the real text; create = full new file.
- Re-validation at write time: normalized safe path, forbidden-target check, `resolve_within`.
- Limits: a run-level model-call budget, 2 attempts per change, abort after repeated AI-server
  failures, files over 100 KB are not edited automatically, generated files capped at 200 KB.
- Context per change: the target file, files cited by its findings, dependency files, and files
  written earlier in the same build. Plan and finding text is sent as untrusted data.
- Prompt version: `build-v1` (`llm/prompts/build.py`).

## 13. Step 5 notes
- `execution/ecosystems.py` chooses fixed commands per ecosystem (Python: compile, install, pytest;
  Node: npm install/ci, npm test). The model never writes a command. Unsupported ecosystems,
  missing tests and sandbox problems produce `unverified`, never `passed`.
- `execution/sandbox.py` is the only module that imports `modal`. It uploads the build copy
  (no `.git`, dependency dirs, symlinks or sensitive files), restricts outbound network to the
  ecosystem's registries with `outbound_domain_allowlist`, and refuses to run if that cannot be
  applied. Output is redacted and bounded.
- `agent/verifier.py` runs rounds; a failed round may trigger `agent/repair.py`, bounded by
  `ASE_MAX_REPAIR_ATTEMPTS`, restricted to the plan's approved files, with the failure output sent
  as untrusted data. A repair that cannot be applied ends the loop.
- States: BUILT -> VERIFYING <-> REPAIRING -> COMPLETED (final status `completed_verified` or
  `completed_unverified`) or FAILED.
- Known limits: install scripts of dependencies run inside the sandbox; the Modal-specific code
  needs a real account to test (`scripts/sandbox_selftest.py`).
