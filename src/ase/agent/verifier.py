"""Step 5: run the project's own checks in an isolated sandbox, with a bounded repair loop.

Honesty rules enforced here:
- "passed" requires that the project's tests really ran in the sandbox and passed, with nothing
  left unverified. Missing tests, unsupported ecosystems and sandbox problems give "unverified",
  never "passed".
- A sandbox failure is not a test failure; it is reported as unverified, not as failed code.
- The repair loop is bounded and stops as soon as a repair cannot be applied.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ase.agent.llm_json import Usage
from ase.agent.repair import Repairer, failing_checks
from ase.core.config import Settings
from ase.core.errors import SandboxError
from ase.core.models import (
    CheckResult,
    CheckStatus,
    Plan,
    VerifyReport,
    VerifyRound,
)
from ase.execution.ecosystems import EcosystemProfile, plan_verification
from ase.execution.sandbox import SandboxRunner
from ase.llm.retry import ModelCallBudget
from ase.repository.inventory import build_inventory

VERIFY_LIMITATIONS = [
    "Only Python and Node projects are verified automatically. Other ecosystems are reported as "
    "unverified, never as passed.",
    "'Passed' means the project's own tests passed in the sandbox. It does not prove the code is "
    "correct beyond what those tests cover.",
    "Dependency install scripts ran inside the sandbox (registry-only network, no secrets).",
]


@dataclass(frozen=True)
class VerifyLimits:
    max_repair_attempts: int
    tests_timeout_seconds: int
    max_files: int
    max_total_bytes: int
    max_file_bytes: int

    @classmethod
    def from_settings(cls, s: Settings) -> VerifyLimits:
        return cls(
            s.max_repair_attempts,
            s.max_exec_seconds,
            s.max_files,
            s.max_repo_bytes,
            s.max_file_bytes,
        )


def _result(profile: str, spec_name: str, command: str, base: CheckResult) -> CheckResult:
    return CheckResult(
        name=f"{profile}:{spec_name}",
        command=command,
        status=base.status,
        exit_code=base.exit_code,
        output=base.output,
        duration_seconds=base.duration_seconds,
    )


def _skipped(profile: str, name: str, command: str, reason: str) -> CheckResult:
    return CheckResult(
        name=f"{profile}:{name}", command=command, status=CheckStatus.SKIPPED, output=reason
    )


Outcome = Literal["passed", "failed", "unverified"]


def compute_outcome(checks: list[CheckResult], reasons: list[str]) -> Outcome:
    """``failed`` > ``passed`` > ``unverified``. Passed needs real tests and nothing unproven."""
    if any(c.status is CheckStatus.FAILED for c in checks):
        return "failed"
    tests_passed = any(
        c.name.endswith(":tests") and c.status is CheckStatus.PASSED for c in checks
    )
    return "passed" if tests_passed and not reasons else "unverified"


class Verifier:
    def __init__(
        self, runner: SandboxRunner, repairer: Repairer | None, limits: VerifyLimits
    ) -> None:
        self._runner = runner
        self._repairer = repairer
        self._limits = limits

    # ------------------------------------------------------------------ public

    def verify(
        self,
        *,
        build_dir: Path,
        plan: Plan,
        budget: ModelCallBudget,
        progress: Callable[[str], None] | None = None,
        on_state: Callable[[str], None] | None = None,
    ) -> VerifyReport:
        say = progress or (lambda message: None)
        set_state = on_state or (lambda state: None)
        usage = Usage()
        rounds: list[VerifyRound] = []
        repairs_used = 0
        registries: dict[str, tuple[str, ...]] = {}

        while True:
            set_state("verifying")
            say(f"round {len(rounds) + 1}: running checks in the sandbox")
            current = self._run_round(build_dir, len(rounds) + 1, say, registries)
            rounds.append(current)
            if (
                current.outcome != "failed"
                or self._repairer is None
                or repairs_used >= self._limits.max_repair_attempts
            ):
                break
            set_state("repairing")
            say(f"repair {repairs_used + 1} of {self._limits.max_repair_attempts}")
            outcome = self._repairer.repair(
                build_dir=build_dir,
                plan=plan,
                failing=failing_checks(current.checks),
                attempt=repairs_used + 1,
                budget=budget,
            )
            usage.merge(outcome.usage)
            current.repaired = outcome.changes
            if outcome.applied == 0:
                break  # the model could not produce a valid fix; do not loop on nothing
            repairs_used += 1

        policy = "; ".join(
            f"{name}: outbound TLS only to {', '.join(hosts)}" for name, hosts in registries.items()
        )
        return VerifyReport(
            rounds=rounds,
            outcome=rounds[-1].outcome,
            max_repair_attempts=self._limits.max_repair_attempts,
            repair_attempts_used=repairs_used,
            sandbox=self._runner.name,
            network_policy=policy or "no network was needed",
            provider=self._repairer.provider_name if self._repairer else "",
            model=usage.model if usage.model != "n/a" else "",
            model_calls=budget.used,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            limitations=list(VERIFY_LIMITATIONS),
        )

    # ----------------------------------------------------------------- helpers

    def _run_round(
        self,
        build_dir: Path,
        index: int,
        say: Callable[[str], None],
        registries: dict[str, tuple[str, ...]],
    ) -> VerifyRound:
        lim = self._limits
        inventory = build_inventory(
            build_dir,
            max_files=lim.max_files,
            max_total_bytes=lim.max_total_bytes,
            max_file_bytes=lim.max_file_bytes,
        )
        vplan = plan_verification(
            inventory, build_dir, tests_timeout_seconds=lim.tests_timeout_seconds
        )
        checks: list[CheckResult] = []
        reasons = list(vplan.unsupported)
        for profile in vplan.profiles:
            try:
                checks.extend(self._run_profile(build_dir, profile, say, registries))
            except SandboxError as exc:
                reasons.append(f"{profile.name}: the sandbox had a problem: {exc.message}")
        ran_tests = any(
            c.name.endswith(":tests") and c.status is not CheckStatus.SKIPPED for c in checks
        )
        any_failed = any(c.status is CheckStatus.FAILED for c in checks)
        if checks and not ran_tests and not any_failed:
            reasons.append("No tests ran, so only the basic checks above could be verified.")
        return VerifyRound(
            index=index,
            checks=checks,
            outcome=compute_outcome(checks, reasons),
            unverified_reasons=reasons,
        )

    def _run_profile(
        self,
        build_dir: Path,
        profile: EcosystemProfile,
        say: Callable[[str], None],
        registries: dict[str, tuple[str, ...]],
    ) -> list[CheckResult]:
        runnable = [c for c in profile.checks if not c.skip_reason]
        results: list[CheckResult] = []
        if not runnable:
            return [
                _skipped(profile.name, c.name, c.command, c.skip_reason) for c in profile.checks
            ]
        registries[profile.name] = profile.registries
        failed_earlier = False
        with self._runner.open(build_dir, profile) as session:
            for spec in profile.checks:
                if spec.skip_reason:
                    reason = spec.skip_reason
                    results.append(_skipped(profile.name, spec.name, spec.command, reason))
                    continue
                if failed_earlier:
                    results.append(
                        _skipped(profile.name, spec.name, spec.command, "An earlier check failed.")
                    )
                    continue
                say(f"{profile.name}: {spec.name}")
                result = _result(profile.name, spec.name, spec.command, session.run(spec))
                results.append(result)
                failed_earlier = result.status is CheckStatus.FAILED
        return results
