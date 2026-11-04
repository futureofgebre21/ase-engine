"""Diagnostic: prove that the Modal Sandbox works end to end on THIS machine and account.

Run:  python scripts/sandbox_selftest.py

This is a health check, not part of the product. It creates two tiny throwaway Python projects
in a temporary folder and runs them through ASE's real verification code and the real Modal
Sandbox, then checks three facts:
  1. a project whose tests pass is reported PASSED,
  2. a project whose test fails is reported FAILED (so exit codes are real, not assumed),
  3. the sandbox cannot reach a site that is not on the package-registry allowlist.
It uses a few cents of Modal credit.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from ase.agent.verifier import Verifier, VerifyLimits
from ase.core.errors import AseError
from ase.core.models import Plan
from ase.execution.ecosystems import CheckSpec, plan_verification
from ase.execution.sandbox import ModalSandboxRunner
from ase.llm.retry import ModelCallBudget
from ase.repository.inventory import build_inventory

LIMITS = VerifyLimits(0, 120, 1_000, 10_000_000, 1_000_000)
PLAN = Plan(provider="selftest", model="none", prompt_version="none")

PASSING = {
    "calc.py": "def add(a, b):\n    return a + b\n",
    "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
}
FAILING = {
    "calc.py": "def add(a, b):\n    return a + b\n",
    "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 6\n",
}
BLOCKED_PROBE = (
    "import sys, urllib.request\n"
    "try:\n"
    "    urllib.request.urlopen('https://example.com', timeout=15)\n"
    "except Exception:\n"
    "    sys.exit(0)  # the request failed: the network is restricted, as intended\n"
    "sys.exit(3)\n"
)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).write_text(text)


def _line(ok: bool, text: str) -> bool:
    print(f"  [{'OK' if ok else 'PROBLEM'}] {text}")
    return ok


def main() -> int:
    runner = ModalSandboxRunner()
    good = True
    try:
        for label, files, expected in (
            ("passing project", PASSING, "passed"),
            ("failing project", FAILING, "failed"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write(root, files)
                print(f"Running the {label} in a Modal Sandbox (the first run builds an image)...")
                report = Verifier(runner, None, LIMITS).verify(
                    build_dir=root, plan=PLAN, budget=ModelCallBudget(1)
                )
                for check in report.rounds[0].checks:
                    print(f"    {check.name}: {check.status.value} (exit {check.exit_code})")
                good &= _line(report.outcome == expected, f"{label}: outcome is {report.outcome}")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, PASSING)
            inv = build_inventory(root, max_files=100, max_total_bytes=10**6, max_file_bytes=10**6)
            profile = plan_verification(inv, root, tests_timeout_seconds=60).profiles[0]
            probe = CheckSpec("probe", ("python", "-c", BLOCKED_PROBE), 60)
            with runner.open(root, profile) as session:
                result = session.run(probe)
            good &= _line(
                result.exit_code == 0, "a site outside the allowlist (example.com) is unreachable"
            )
    except AseError as exc:
        print(f"\nThe sandbox could not be used: {exc.message}")
        return 1
    print("\nSandbox self-test:", "PASSED" if good else "FAILED")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
