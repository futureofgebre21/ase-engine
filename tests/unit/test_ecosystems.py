import json
import shutil
from pathlib import Path

from ase.execution.ecosystems import SUPPORTED, plan_verification
from ase.repository.inventory import build_inventory

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"


def _plan(root, timeout=120):
    inv = build_inventory(root, max_files=1000, max_total_bytes=10**7, max_file_bytes=10**6)
    return plan_verification(inv, root, tests_timeout_seconds=timeout)


def _write(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _names(profile):
    return [c.name for c in profile.checks]


def test_python_fixture_gets_compile_install_runner_and_tests(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    plan = _plan(root)
    assert plan.unsupported == () and len(plan.profiles) == 1
    py = plan.profiles[0]
    assert (py.name, py.image, py.cwd) == ("python", "python", ".")
    assert _names(py) == ["compile", "install", "install-test-runner", "tests"]
    assert py.registries == ("pypi.org", "files.pythonhosted.org")
    tests = py.checks[-1]
    assert tests.argv[:3] == ("python", "-m", "pytest") and tests.skip_reason == ""
    assert py.checks[1].argv[-2:] == ("-r", "requirements.txt")


def test_commands_are_fixed_argument_lists_never_model_text(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    for profile in _plan(root).profiles:
        for check in profile.checks:
            assert isinstance(check.argv, tuple) and all(isinstance(a, str) for a in check.argv)
            assert check.timeout_seconds > 0


def test_python_without_tests_marks_tests_skipped_not_passed(tmp_path):
    _write(tmp_path, {"app.py": "print(1)", "requirements.txt": "requests\n"})
    py = _plan(tmp_path).profiles[0]
    assert _names(py) == ["compile", "install", "tests"]
    assert py.checks[-1].skip_reason == "No Python test files found."


def test_python_scripts_without_a_manifest_are_still_checked(tmp_path):
    _write(tmp_path, {"main.py": "print(1)", "test_main.py": "def test_x(): pass"})
    py = _plan(tmp_path).profiles[0]
    assert _names(py) == ["compile", "install-test-runner", "tests"]  # nothing to install


def test_pyproject_projects_are_installed_as_packages(tmp_path):
    _write(tmp_path, {"pyproject.toml": "[project]\nname='x'\n", "t/test_a.py": "x=1"})
    py = _plan(tmp_path).profiles[0]
    assert py.checks[1].name == "install" and py.checks[1].argv[-1] == "."


def test_node_with_a_real_test_script(tmp_path):
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"test": "jest"}}), "a.js": "1"})
    node = _plan(tmp_path).profiles[0]
    assert (node.name, node.image) == ("node", "node")
    assert node.registries == ("registry.npmjs.org",)
    assert node.checks[0].argv[:2] == ("npm", "install")
    assert node.checks[-1].argv == ("npm", "test") and node.checks[-1].skip_reason == ""


def test_node_uses_npm_ci_when_a_lockfile_exists(tmp_path):
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"test": "jest"}}),
                      "package-lock.json": "{}"})  # fmt: skip
    assert _plan(tmp_path).profiles[0].checks[0].argv[:2] == ("npm", "ci")


def test_node_without_a_real_test_script_is_skipped(tmp_path):
    for scripts in ({}, {"test": 'echo "Error: no test specified" && exit 1'}, {"test": " "}):
        sub = tmp_path / str(len(str(scripts)))
        _write(sub, {"package.json": json.dumps({"scripts": scripts}), "a.js": "1"})
        assert "real" in _plan(sub).profiles[0].checks[-1].skip_reason


def test_unsupported_ecosystems_are_reported_unverified(tmp_path):
    _write(tmp_path, {"go.mod": "module x", "main.go": "package main",
                      "main_test.go": "package main"})  # fmt: skip
    plan = _plan(tmp_path)
    assert plan.profiles == ()
    assert plan.unsupported == ("No automatic test runner for the 'go' ecosystem yet.",)


def test_mixed_repositories_get_one_profile_per_supported_ecosystem(tmp_path):
    _write(tmp_path, {
        "api/requirements.txt": "flask", "api/app.py": "x=1", "api/test_app.py": "x=1",
        "web/package.json": json.dumps({"scripts": {"test": "vitest"}}),
        "svc/go.mod": "module x",
    })  # fmt: skip
    plan = _plan(tmp_path)
    assert sorted(p.name for p in plan.profiles) == ["node", "python"]
    assert {p.name: p.cwd for p in plan.profiles} == {"python": "api", "node": "web"}
    assert len(plan.unsupported) == 1 and "go" in plan.unsupported[0]


def test_nothing_recognisable_is_reported(tmp_path):
    _write(tmp_path, {"notes.txt": "hello"})
    plan = _plan(tmp_path)
    assert plan.profiles == () and "No supported project type" in plan.unsupported[0]


def test_supported_list_matches_what_is_implemented():
    assert SUPPORTED == ("python", "node")
