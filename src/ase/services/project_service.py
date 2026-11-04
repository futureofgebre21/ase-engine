"""Application service: the only thing the UI talks to.

The UI never touches storage, security helpers, or domain rules directly.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial

from pydantic import BaseModel, ConfigDict

from ase.agent.auditor import AuditLimits, Auditor
from ase.agent.builder import Builder, BuildLimits
from ase.agent.planner import Planner, PlanLimits
from ase.agent.repair import Repairer, RepairLimits
from ase.agent.verifier import Verifier, VerifyLimits
from ase.core.config import STAGE_ORDER, Settings
from ase.core.errors import AseError, PolicyViolationError, RunNotFoundError
from ase.core.models import ActionRecord, RunRecord
from ase.core.states import RunState
from ase.llm.base import LLMProvider
from ase.execution.sandbox import ModalSandboxRunner, SandboxRunner
from ase.llm.factory import build_provider, build_secondary_provider
from ase.llm.retry import ModelCallBudget
from ase.repository.build_workspace import BuildWorkspace
from ase.repository.gitclone import clone_with_git
from ase.repository.importer import ImportLimits, RepositoryImporter
from ase.security.redaction import redact
from ase.security.urls import parse_github_repo_url
from ase.storage.run_store import JsonRunStore

UNTRUSTED_NOTE = "Repository content is untrusted. ASE has not executed any of it."
NO_PROVIDER_NOTE = (
    "No AI provider is configured, so the audit step was not run. "
    "Set ASE_LLM_PROVIDER, ASE_LLM_BASE_URL, ASE_LLM_MODEL and ASE_LLM_API_KEY in .env."
)
NEXT_STEPS_NOTE = "The code is built but NOT verified. Step 6 (push and deploy) is not built yet."
STEP6_NOTE = "Step 6 (push and deploy) is not implemented yet."
EXECUTION_DISABLED_NOTE = (
    "Tests were not run: code execution is disabled. Set ASE_ALLOW_CODE_EXECUTION=true to run "
    "them in an isolated Modal Sandbox (package-registry network only, no secrets)."
)
NOTHING_TO_PLAN_NOTE = "The audit found nothing to change, so there is nothing to plan."

# Callback(stage, status): stage is "import", "audit", "plan", "build" or "verify". Status is
# "running", "done", "failed" or "skipped" (build and verify also report one line per change or
# check). It is called when a stage STARTS and when it ENDS, never early.
StageCallback = Callable[[str, str], None]


class StageRoute(BaseModel):
    """Which AI provider and model a step uses (read from configuration)."""

    model_config = ConfigDict(frozen=True)

    stage: str
    provider: str
    model: str | None


class EnvironmentStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    routes: list[StageRoute]

    llm_configured: bool
    llm_provider: str
    llm_model: str | None
    github_token_configured: bool
    docker_cli_found: bool  # only means a `docker` executable is on PATH, not that it works
    code_execution_available: bool
    git_revocation_check_enabled: bool
    code_execution_note: str


class ProjectService:
    def __init__(
        self,
        settings: Settings,
        store: JsonRunStore,
        importer: RepositoryImporter | None = None,
        provider: LLMProvider | None = None,
        secondary_provider: LLMProvider | None = None,
        sandbox_runner: SandboxRunner | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._importer = importer or RepositoryImporter(
            settings.workspace_root,
            ImportLimits.from_settings(settings),
            partial(clone_with_git, check_revocation=settings.git_check_cert_revocation),
        )
        main = provider if provider is not None else build_provider(settings)
        second = secondary_provider
        if second is None:
            second = build_secondary_provider(settings)
        routed_to_second = settings.llm2_stage_set if second is not None else frozenset()
        # One provider per AI step: the second provider for the stages routed to it, else the main.
        self._providers: dict[str, LLMProvider | None] = {
            stage: second if stage in routed_to_second else main for stage in STAGE_ORDER
        }
        audit_p, plan_p, build_p = (self._providers[name] for name in STAGE_ORDER)
        self._auditor = Auditor(audit_p, AuditLimits.from_settings(settings)) if audit_p else None
        self._planner = Planner(plan_p, PlanLimits.from_settings(settings)) if plan_p else None
        self._builder = Builder(build_p, BuildLimits.from_settings(settings)) if build_p else None
        self._build_workspace = BuildWorkspace(settings.workspace_root)
        runner = sandbox_runner
        if runner is None and settings.allow_code_execution:
            runner = ModalSandboxRunner()
        repairer = Repairer(build_p, RepairLimits.from_settings(settings)) if build_p else None
        self._verifier = (
            Verifier(runner, repairer, VerifyLimits.from_settings(settings))
            if runner is not None
            else None
        )

    def environment_status(self) -> EnvironmentStatus:
        return EnvironmentStatus(
            routes=[
                StageRoute(
                    stage=stage,
                    provider=p.name,
                    model=getattr(p, "model_name", None),
                )
                for stage, p in self._providers.items()
                if p is not None
            ],
            llm_configured=all(p is not None for p in self._providers.values()),
            llm_provider=self._settings.llm_provider,
            llm_model=self._settings.llm_model,
            github_token_configured=self._settings.github_token is not None,
            docker_cli_found=shutil.which("docker") is not None,
            # No sandbox exists yet, so this is False regardless of settings.
            code_execution_available=self._verifier is not None,
            git_revocation_check_enabled=self._settings.git_check_cert_revocation,
            code_execution_note=(
                "Tests run in an isolated Modal Sandbox (package-registry network only, "
                "no secrets)."
                if self._verifier is not None
                else "Disabled. Set ASE_ALLOW_CODE_EXECUTION=true to run tests in a sandbox."
            ),
        )

    def start_import(self, url: str) -> RunRecord:
        """Validate the URL, clone it into an isolated workspace, and inventory it.

        Raises ``UnsafeUrlError`` for rejected URLs (no run is created). Any later failure
        is recorded on the run (state FAILED) instead of being raised.
        """
        owner, repo = parse_github_repo_url(url)
        run = RunRecord(source=f"github.com/{owner}/{repo}")
        now = datetime.now(UTC)
        run.actions.append(
            ActionRecord(
                name="validate_repository_url",
                started_at=now,
                ended_at=now,
                summary="URL passed validation.",
            )
        )
        self._store.save(run)  # recorded first so a crash still leaves a trace

        action = ActionRecord(name="import_repository")
        run.actions.append(action)
        try:
            _, inventory = self._importer.import_repo(url, run.run_id)
        except (AseError, OSError) as exc:
            message = redact(getattr(exc, "message", str(exc)))
            action.ended_at = datetime.now(UTC)
            action.exit_status = "error"
            action.summary = message
            run.transition(RunState.FAILED, reason=message)
        else:
            action.ended_at = datetime.now(UTC)
            action.summary = (
                f"Cloned and inventoried {inventory.total_files} files "
                f"({inventory.total_bytes} bytes)."
            )
            run.inventory = inventory
            run.limitations.append(UNTRUSTED_NOTE)
            run.limitations.extend(inventory.notes)
            run.transition(RunState.IMPORTED, reason="Repository cloned and inventoried")
        self._store.save(run)
        return run

    def run_audit(self, run_id: str) -> RunRecord:
        """Audit an imported repository. A failed audit is recorded as an action and the run
        stays IMPORTED so the user can retry; it never produces partial findings."""
        if self._auditor is None:
            raise PolicyViolationError(NO_PROVIDER_NOTE)
        run = self.get_run(run_id)
        if run.state is not RunState.IMPORTED or run.inventory is None:
            raise PolicyViolationError("Only a freshly imported repository can be audited.")
        workspace = self._importer.workspace_path(run.run_id)
        if not workspace.is_dir():
            raise PolicyViolationError("The repository workspace no longer exists.")

        action = ActionRecord(name="ai_audit")
        run.actions.append(action)
        try:
            report = self._auditor.audit(workspace, run.inventory)
        except (AseError, OSError) as exc:
            action.ended_at = datetime.now(UTC)
            action.exit_status = "error"
            action.summary = redact(getattr(exc, "message", str(exc)))
            self._store.save(run)
            raise
        action.ended_at = datetime.now(UTC)
        action.summary = (
            f"{len(report.findings)} finding(s) kept, {report.rejected_findings} rejected; "
            f"{report.model_calls} model call(s)"
        )
        run.audit = report
        run.transition(RunState.ANALYZED, reason="Audit completed")
        self._store.save(run)
        return run

    def run_plan(self, run_id: str) -> RunRecord:
        """Step 3: turn the verified audit findings into a validated change plan.

        A failed attempt is recorded as an action and the run stays ANALYZED (retryable).
        """
        if self._planner is None:
            raise PolicyViolationError(NO_PROVIDER_NOTE)
        run = self.get_run(run_id)
        if run.state is not RunState.ANALYZED or run.inventory is None or run.audit is None:
            raise PolicyViolationError("Only an audited repository can be planned.")
        if not run.audit.findings:
            raise PolicyViolationError(NOTHING_TO_PLAN_NOTE)
        workspace = self._importer.workspace_path(run.run_id)
        if not workspace.is_dir():
            raise PolicyViolationError("The repository workspace no longer exists.")

        action = ActionRecord(name="architecture_plan")
        run.actions.append(action)
        try:
            plan = self._planner.plan(workspace, run.inventory, run.audit.findings)
        except (AseError, OSError) as exc:
            action.ended_at = datetime.now(UTC)
            action.exit_status = "error"
            action.summary = redact(getattr(exc, "message", str(exc)))
            self._store.save(run)
            raise
        action.ended_at = datetime.now(UTC)
        action.summary = (
            f"{len(plan.changes)} change(s) planned, {plan.rejected_changes} rejected, "
            f"{len(plan.deferred)} finding(s) deferred; {plan.model_calls} model call(s)"
        )
        if not plan.changes:
            action.exit_status = "error"
            action.summary = "The model's plan contained no valid changes. " + action.summary
            self._store.save(run)
            raise PolicyViolationError("The plan contained no valid changes. Try again.")
        run.plan = plan
        run.transition(RunState.PLAN_READY, reason="Plan completed")
        self._store.save(run)
        return run

    def run_build(
        self, run_id: str, progress: Callable[[str], None] | None = None
    ) -> RunRecord:
        """Step 4: write the planned changes into an isolated copy of the clone.

        The untouched clone stays as the baseline. A build in which no change could be applied
        ends the run as FAILED; partial success is reported change by change.
        """
        if self._builder is None:
            raise PolicyViolationError(NO_PROVIDER_NOTE)
        run = self.get_run(run_id)
        if (
            run.state is not RunState.PLAN_READY
            or run.plan is None
            or run.inventory is None
            or run.audit is None
        ):
            raise PolicyViolationError("Only a planned repository can be built.")
        pristine = self._importer.workspace_path(run.run_id)
        if not pristine.is_dir():
            raise PolicyViolationError("The repository workspace no longer exists.")

        run.approved_scope = [c.path for c in run.plan.changes]
        run.transition(
            RunState.AWAITING_APPROVAL,
            reason="Build requested by the user; scope: planned files, isolated copy only",
        )
        run.transition(RunState.EXECUTING, reason="Approved by the user's request to build")
        action = ActionRecord(name="build")
        run.actions.append(action)
        self._store.save(run)

        report = None
        try:
            build_dir = self._build_workspace.prepare(pristine, run.run_id)
            report = self._builder.build(
                build_dir=build_dir,
                build_name=self._build_workspace.relative_name(run.run_id),
                plan=run.plan,
                findings=run.audit.findings,
                inventory=run.inventory,
                progress=progress,
            )
            failure = "" if report.applied else "No planned change could be applied."
        except (AseError, OSError) as exc:
            failure = redact(getattr(exc, "message", str(exc)))
        action.ended_at = datetime.now(UTC)
        if failure or report is None:
            failure = failure or "The build produced no report."
            action.exit_status = "error"
            action.summary = failure
            if report is not None:
                run.build = report  # keep the per-change reasons visible
            run.transition(RunState.FAILED, reason=failure)
        else:
            action.summary = (
                f"{report.applied} applied, {report.failed} failed, {report.skipped} skipped; "
                f"{report.model_calls} model call(s)"
            )
            run.build = report
            run.transition(RunState.BUILT, reason="Code written; not yet verified")
        self._store.save(run)
        return run

    def run_verify(
        self, run_id: str, progress: Callable[[str], None] | None = None
    ) -> RunRecord:
        """Step 5: run the built project's checks in an isolated sandbox, with bounded repair.

        Ends COMPLETED (verified, or honestly unverified) or FAILED (a check really failed and
        the bounded repairs did not fix it). Code runs only inside the sandbox, never here.
        """
        if self._verifier is None:
            raise PolicyViolationError(EXECUTION_DISABLED_NOTE)
        run = self.get_run(run_id)
        if run.state is not RunState.BUILT or run.plan is None or run.build is None:
            raise PolicyViolationError("Only a built repository can be verified.")
        build_dir = self._build_workspace.path(run.run_id)
        if not build_dir.is_dir():
            raise PolicyViolationError("The build copy no longer exists.")

        run.transition(RunState.VERIFYING, reason="Verification started; sandbox, no secrets")
        action = ActionRecord(name="verify")
        run.actions.append(action)
        self._store.save(run)

        def on_state(phase: str) -> None:
            target = RunState.REPAIRING if phase == "repairing" else RunState.VERIFYING
            if run.state is not target:
                run.transition(target, reason=f"Verification phase: {phase}")

        try:
            report = self._verifier.verify(
                build_dir=build_dir,
                plan=run.plan,
                budget=ModelCallBudget(self._settings.max_model_calls),
                progress=progress,
                on_state=on_state,
            )
        except (AseError, OSError) as exc:
            message = redact(getattr(exc, "message", str(exc)))
            action.ended_at = datetime.now(UTC)
            action.exit_status = "error"
            action.summary = message
            run.transition(RunState.FAILED, reason=message)
            self._store.save(run)
            return run

        run.verification = report
        action.ended_at = datetime.now(UTC)
        last = report.rounds[-1]
        action.summary = (
            f"outcome: {report.outcome}; {len(report.rounds)} round(s), "
            f"{report.repair_attempts_used} repair(s)"
        )
        if report.outcome == "failed":
            action.exit_status = "error"
            run.transition(
                RunState.FAILED,
                reason=f"Checks still failing after {report.repair_attempts_used} repair(s)",
            )
        elif report.outcome == "passed":
            run.transition(RunState.COMPLETED, reason="Verified: the tests passed in the sandbox")
            run.final_status = "completed_verified"
        else:
            run.limitations.extend(last.unverified_reasons)
            run.transition(RunState.COMPLETED, reason="Finished, but could not be fully verified")
            run.final_status = "completed_unverified"
        self._store.save(run)
        return run

    def run_pipeline(self, url: str, on_stage: StageCallback | None = None) -> RunRecord:
        """Steps 1-5 end to end, automatically: import, audit, plan, build, then verify.

        Every stage reports real status through ``on_stage``. A failed stage stops the
        pipeline; nothing later runs and nothing is reported as done that did not happen.
        """

        def notify(stage: str, status: str) -> None:
            if on_stage is not None:
                on_stage(stage, status)

        notify("import", "running")
        run = self.start_import(url)  # raises UnsafeUrlError for a rejected URL
        if run.state is not RunState.IMPORTED:
            notify("import", "failed")
            return run
        notify("import", "done")

        if self._auditor is None:
            run.limitations.append(NO_PROVIDER_NOTE)
            self._store.save(run)
            notify("audit", "skipped")
            return run

        notify("audit", "running")
        try:
            run = self.run_audit(run.run_id)
        except (AseError, OSError):
            notify("audit", "failed")  # run_audit already recorded the error on the run
            return self.get_run(run.run_id)
        notify("audit", "done")

        if run.audit is None or not run.audit.findings:
            run.limitations.append(NOTHING_TO_PLAN_NOTE)
            self._store.save(run)
            notify("plan", "skipped")
            return run

        notify("plan", "running")
        try:
            run = self.run_plan(run.run_id)
        except (AseError, OSError):
            notify("plan", "failed")  # run_plan already recorded the error on the run
            return self.get_run(run.run_id)
        notify("plan", "done")

        notify("build", "running")
        try:
            run = self.run_build(run.run_id, lambda message: notify("build", message))
        except (AseError, OSError):
            notify("build", "failed")
            return self.get_run(run.run_id)
        if run.state is not RunState.BUILT:
            notify("build", "failed")
            return run
        notify("build", "done")

        if self._verifier is None:
            run.limitations.extend([EXECUTION_DISABLED_NOTE, NEXT_STEPS_NOTE])
            self._store.save(run)
            notify("verify", "skipped")
            return run

        notify("verify", "running")
        try:
            run = self.run_verify(run.run_id, lambda message: notify("verify", message))
        except (AseError, OSError):
            notify("verify", "failed")
            return self.get_run(run.run_id)
        run.limitations.append(STEP6_NOTE)
        self._store.save(run)
        outcome = run.verification.outcome if run.verification else "failed"
        notify("verify", "failed" if outcome == "failed" else "done")
        return run

    def list_runs(self, limit: int = 50) -> list[RunRecord]:
        return self._store.list_runs(limit)

    def get_run(self, run_id: str) -> RunRecord:
        run = self._store.load(run_id)
        if run is None:
            raise RunNotFoundError("Run not found.")
        return run

    def cancel_run(self, run_id: str) -> RunRecord:
        run = self.get_run(run_id)
        run.transition(RunState.CANCELLED, reason="Cancelled by user")
        self._store.save(run)
        return run
