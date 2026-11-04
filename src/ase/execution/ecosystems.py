"""Decide, deterministically, which checks ASE can run for a project.

Every command here is a fixed argument list chosen by this module. The model never writes a
command. Ecosystems without a runner here are reported as "unverified", never as passed.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from ase.core.models import RepositoryInventory

INSTALL_TIMEOUT_SECONDS = 300
COMPILE_TIMEOUT_SECONDS = 60
PYTHON_REGISTRIES = ("pypi.org", "files.pythonhosted.org")
NODE_REGISTRIES = ("registry.npmjs.org",)
SUPPORTED = ("python", "node")


@dataclass(frozen=True)
class CheckSpec:
    name: str  # "compile", "install", "install-test-runner", "tests"
    argv: tuple[str, ...]
    timeout_seconds: int
    skip_reason: str = ""  # when set, the check is reported SKIPPED and never run

    @property
    def command(self) -> str:
        return shlex.join(self.argv)


@dataclass(frozen=True)
class EcosystemProfile:
    name: str  # "python" or "node"
    image: Literal["python", "node"]
    cwd: str  # directory of the manifest, relative to the project root ("." for the root)
    registries: tuple[str, ...]  # the only hosts the sandbox may reach (TLS, port 443)
    checks: tuple[CheckSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class VerificationPlan:
    profiles: tuple[EcosystemProfile, ...]
    unsupported: tuple[str, ...]  # human-readable reasons, reported as "unverified"


def _shallowest(paths: list[str], names: frozenset[str]) -> str | None:
    """Directory of the shallowest file whose lower-case name is in ``names``."""
    hits = [p for p in paths if PurePosixPath(p).name.lower() in names]
    if not hits:
        return None
    best = min(hits, key=lambda p: (len(PurePosixPath(p).parts), p))
    parent = PurePosixPath(best).parent.as_posix()
    return parent


def _in_dir(path: str, cwd: str) -> bool:
    return cwd == "." or path == cwd or path.startswith(cwd + "/")


def _files_directly_in(inventory: RepositoryInventory, cwd: str) -> set[str]:
    """Lower-case names of the usable files that sit directly in ``cwd``."""
    return {
        PurePosixPath(e.path).name.lower()
        for e in inventory.files
        if e.excluded is None and PurePosixPath(e.path).parent.as_posix() == cwd
    }


def _python_profile(
    inventory: RepositoryInventory, build_dir: Path, tests_timeout: int
) -> EcosystemProfile:
    manifest_names = frozenset({"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"})
    cwd = _shallowest(inventory.dependency_files, manifest_names) or "."
    names = _files_directly_in(inventory, cwd)
    py_tests = [t for t in inventory.test_files if t.endswith(".py") and _in_dir(t, cwd)]

    pip = ("python", "-m", "pip", "install", "--no-input", "--disable-pip-version-check", "-q")
    checks: list[CheckSpec] = [
        CheckSpec("compile", ("python", "-m", "compileall", "-q", "."), COMPILE_TIMEOUT_SECONDS)
    ]
    pyproject_text = ""
    if "pyproject.toml" in names:
        try:
            pyproject_text = (build_dir / cwd / "pyproject.toml").read_text(errors="replace")
        except OSError:
            pyproject_text = ""
    installable = "[project]" in pyproject_text or "[build-system]" in pyproject_text
    if "requirements.txt" in names:
        requirements = (*pip, "-r", "requirements.txt")
        checks.append(CheckSpec("install", requirements, INSTALL_TIMEOUT_SECONDS))
    elif "setup.py" in names or installable:
        checks.append(CheckSpec("install", (*pip, "."), INSTALL_TIMEOUT_SECONDS))
    if py_tests:
        checks.append(CheckSpec("install-test-runner", (*pip, "pytest"), INSTALL_TIMEOUT_SECONDS))
        pytest = ("python", "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider")
        checks.append(CheckSpec("tests", pytest, tests_timeout))
    else:
        checks.append(
            CheckSpec("tests", ("true",), tests_timeout, skip_reason="No Python test files found.")
        )
    return EcosystemProfile("python", "python", cwd, PYTHON_REGISTRIES, tuple(checks))


def _node_profile(
    inventory: RepositoryInventory, build_dir: Path, tests_timeout: int
) -> EcosystemProfile:
    cwd = _shallowest(inventory.dependency_files, frozenset({"package.json"})) or "."
    manifest = build_dir / cwd / "package.json"
    test_script = ""
    try:
        data = json.loads(manifest.read_text(errors="replace"))
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if isinstance(scripts, dict) and isinstance(scripts.get("test"), str):
            test_script = scripts["test"]
    except (OSError, ValueError):
        test_script = ""
    has_lock = (build_dir / cwd / "package-lock.json").is_file()
    install = ("npm", "ci" if has_lock else "install", "--no-audit", "--no-fund")
    checks = [CheckSpec("install", install, INSTALL_TIMEOUT_SECONDS)]
    if not test_script.strip() or "no test specified" in test_script:
        checks.append(
            CheckSpec(
                "tests", ("true",), tests_timeout,
                skip_reason='package.json has no real "test" script.',
            )
        )  # fmt: skip
    else:
        checks.append(CheckSpec("tests", ("npm", "test"), tests_timeout))
    return EcosystemProfile("node", "node", cwd, NODE_REGISTRIES, tuple(checks))


def plan_verification(
    inventory: RepositoryInventory, build_dir: Path, *, tests_timeout_seconds: int
) -> VerificationPlan:
    """Build the verification plan from the CURRENT files of the build copy."""
    profiles: list[EcosystemProfile] = []
    unsupported: list[str] = []
    ecosystems = list(inventory.ecosystems)
    if not ecosystems and inventory.primary_language == "Python":
        ecosystems = ["python"]  # plain scripts with no manifest
    for eco in ecosystems:
        if eco == "python":
            profiles.append(_python_profile(inventory, build_dir, tests_timeout_seconds))
        elif eco == "node":
            profiles.append(_node_profile(inventory, build_dir, tests_timeout_seconds))
        else:
            unsupported.append(f"No automatic test runner for the '{eco}' ecosystem yet.")
    if not ecosystems:
        unsupported.append("No supported project type (Python or Node) was detected.")
    return VerificationPlan(tuple(profiles), tuple(unsupported))
