"""Isolated execution of a project's checks in a Modal Sandbox.

Why a sandbox: generated and imported code must never run on the host machine. A Modal Sandbox
is a separate gVisor-isolated container with no access to this machine or to the Modal
workspace's resources.

What is uploaded: the build copy only, minus .git, dependency/build directories, symlinks and
sensitive files (.env, keys). No secrets are ever injected into the sandbox.

Network: only the package-registry hosts of the project's ecosystem (TLS, port 443), through
Modal's ``outbound_domain_allowlist``. If the installed Modal client cannot apply that
restriction, execution is refused instead of running with open network access.

This is the only module that imports ``modal``. Everything around it is tested with a test
double; this module itself needs a real Modal account to run (see scripts/sandbox_selftest.py).
"""

from __future__ import annotations

import os
import shlex
import tarfile
import tempfile
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from pathlib import Path
from typing import Any, Protocol

from ase.core.errors import ConfigurationError, SandboxError
from ase.core.models import CheckResult, CheckStatus
from ase.execution.ecosystems import CheckSpec, EcosystemProfile
from ase.repository.inventory import SKIP_DIRS
from ase.security.redaction import is_sensitive_path, redact

MAX_ARCHIVE_BYTES = 50_000_000
MAX_OUTPUT_CHARS = 20_000
_TIMEOUT_EXIT_CODE = 124  # the conventional exit code for "timed out"
_SANDBOX_ENV = (
    "CI=1",
    "PYTHONDONTWRITEBYTECODE=1",
    "PIP_NO_INPUT=1",
    "NPM_CONFIG_UPDATE_NOTIFIER=false",
)
_REMOTE_ARCHIVE = "/tmp/project.tgz"  # noqa: S108 - a path inside the sandbox, not on this machine


class SandboxSession(Protocol):
    def run(self, spec: CheckSpec) -> CheckResult:
        """Run one fixed check and return what really happened (real exit code)."""
        ...


class SandboxRunner(Protocol):
    name: str

    def open(
        self, build_dir: Path, profile: EcosystemProfile
    ) -> AbstractContextManager[SandboxSession]:
        """Start an isolated environment holding a copy of ``build_dir``."""
        ...


def build_archive(build_dir: Path, max_bytes: int = MAX_ARCHIVE_BYTES) -> Path:
    """Pack the build copy into a temporary .tgz. Caller deletes it.

    Skips dependency/build directories, symlinks and sensitive files, so secrets and links
    never leave this machine.
    """
    root = Path(build_dir).resolve(strict=True)
    handle = tempfile.NamedTemporaryFile(prefix="ase-", suffix=".tgz", delete=False)  # noqa: SIM115
    archive = Path(handle.name)
    handle.close()
    total = 0
    try:
        with tarfile.open(archive, "w:gz") as tar:
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                base = Path(dirpath)
                dirnames[:] = sorted(
                    d for d in dirnames if d not in SKIP_DIRS and not (base / d).is_symlink()
                )
                for name in sorted(filenames):
                    full = base / name
                    if full.is_symlink() or not full.is_file():
                        continue
                    rel = full.relative_to(root).as_posix()
                    if is_sensitive_path(rel):
                        continue
                    total += full.stat().st_size
                    if total > max_bytes:
                        raise SandboxError("The project is too large to upload to the sandbox.")
                    info = tar.gettarinfo(str(full), arcname=rel)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with full.open("rb") as fh:
                        tar.addfile(info, fh)
    except BaseException:
        archive.unlink(missing_ok=True)
        raise
    return archive


def _drain(stream: Any) -> str:
    """Read a process stream to the end, keeping only the last ``MAX_OUTPUT_CHARS``."""
    tail = ""
    for chunk in stream:
        text = chunk if isinstance(chunk, str) else bytes(chunk).decode("utf-8", "replace")
        tail = (tail + text)[-MAX_OUTPUT_CHARS:]
    return tail


class _ModalSession:
    def __init__(self, sandbox: Any, cwd: str) -> None:
        self._sb = sandbox
        self._dir = "/work" if cwd in ("", ".") else f"/work/{cwd}"

    def run(self, spec: CheckSpec) -> CheckResult:
        script = f"cd {shlex.quote(self._dir)} && {shlex.join(['env', *_SANDBOX_ENV, *spec.argv])}"
        started = time.monotonic()
        try:
            proc = self._sb.exec("bash", "-c", script, timeout=spec.timeout_seconds)
            stdout = _drain(proc.stdout)
            stderr = _drain(proc.stderr)
            proc.wait()
            code = proc.returncode
        except Exception as exc:
            elapsed = time.monotonic() - started
            if "timeout" in type(exc).__name__.lower():  # the code under test hung: a real failure
                return CheckResult(
                    name=spec.name, command=spec.command, status=CheckStatus.FAILED,
                    exit_code=_TIMEOUT_EXIT_CODE, duration_seconds=elapsed,
                    output=f"Timed out after {spec.timeout_seconds} seconds.",
                )  # fmt: skip
            raise SandboxError(f"The sandbox failed while running '{spec.name}'.") from exc
        if not isinstance(code, int):
            raise SandboxError(f"The sandbox did not report an exit code for '{spec.name}'.")
        output = stdout + (f"\n[stderr]\n{stderr}" if stderr.strip() else "")
        return CheckResult(
            name=spec.name,
            command=spec.command,
            status=CheckStatus.PASSED if code == 0 else CheckStatus.FAILED,
            exit_code=code,
            output=redact(output)[-MAX_OUTPUT_CHARS:],
            duration_seconds=time.monotonic() - started,
        )


class ModalSandboxRunner:
    name = "modal"

    def __init__(self, app_name: str = "ase-verify", max_lifetime_seconds: int = 1_800) -> None:
        self._app_name = app_name
        self._max_lifetime = max_lifetime_seconds

    @staticmethod
    def _image(modal: Any, profile: EcosystemProfile) -> Any:
        if profile.image == "node":
            return modal.Image.from_registry("node:20-slim")
        return modal.Image.debian_slim(python_version="3.12")

    @contextmanager
    def open(self, build_dir: Path, profile: EcosystemProfile) -> Iterator[SandboxSession]:
        try:
            import modal
        except ImportError as exc:
            raise ConfigurationError(
                "The 'modal' package is not installed. Run: pip install modal"
            ) from exc

        archive = build_archive(build_dir)
        sandbox: Any = None
        try:
            runnable = [c for c in profile.checks if not c.skip_reason]
            lifetime = min(self._max_lifetime, sum(c.timeout_seconds for c in runnable) + 300)
            try:
                app = modal.App.lookup(self._app_name, create_if_missing=True)
                sandbox = modal.Sandbox.create(
                    app=app,
                    image=self._image(modal, profile),
                    timeout=lifetime,
                    outbound_domain_allowlist=list(profile.registries),
                )
            except TypeError as exc:  # this Modal client has no domain allowlist: refuse
                raise ConfigurationError(
                    "The installed 'modal' version cannot restrict the sandbox network. "
                    "Run: pip install -U modal"
                ) from exc
            except Exception as exc:
                detail = redact(str(exc))[:300]
                raise SandboxError(f"Could not start a Modal Sandbox: {detail}") from exc

            try:
                sandbox.filesystem.copy_from_local(str(archive), _REMOTE_ARCHIVE)
                unpack = sandbox.exec(
                    "bash", "-c",
                    f"mkdir -p /work && tar -xzf {_REMOTE_ARCHIVE} -C /work --no-same-owner",
                    timeout=120,
                )  # fmt: skip
                unpack.wait()
                unpacked = unpack.returncode == 0
            except Exception as exc:
                detail = redact(str(exc))[:300]
                raise SandboxError(f"Could not upload the project: {detail}") from exc
            if not unpacked:
                raise SandboxError("The project could not be unpacked inside the sandbox.")
            session = _ModalSession(sandbox, profile.cwd)
        except BaseException:
            if sandbox is not None:
                with suppress(Exception):
                    sandbox.terminate()
            archive.unlink(missing_ok=True)
            raise

        try:
            yield session
        finally:
            with suppress(Exception):
                sandbox.terminate()
            archive.unlink(missing_ok=True)
