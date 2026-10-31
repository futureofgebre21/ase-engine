import shutil
from pathlib import Path

import pytest

from ase.core.errors import BudgetExceededError
from ase.core.models import ExclusionReason
from ase.repository.inventory import build_inventory

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"
LIMITS = dict(max_files=1000, max_total_bytes=10_000_000, max_file_bytes=1_000_000)


def _inv(root, **over):
    return build_inventory(root, **{**LIMITS, **over})


def test_fixture_repo_inventory(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    inv = _inv(root)
    by_path = {e.path: e for e in inv.files}
    assert inv.primary_language == "Python" and inv.ecosystems == ["python"]
    assert inv.dependency_files == ["requirements.txt"]
    assert inv.test_files == ["tests/test_cart.py"]
    assert "main.py" in inv.entry_points
    assert "pytest" in inv.framework_hints
    assert by_path[".env.production"].excluded is ExclusionReason.SENSITIVE
    assert by_path["logo.png"].excluded is ExclusionReason.BINARY
    assert by_path["cart.py"].excluded is None


def test_generated_directories_are_skipped_and_git_is_not_listed(tmp_path):
    for d in ("node_modules/pkg", "__pycache__", ".git"):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "f.js").write_text("x")
    (tmp_path / "a.py").write_text("print(1)")
    inv = _inv(tmp_path)
    assert [e.path for e in inv.files] == ["a.py"]
    assert sorted(inv.skipped_directories) == ["__pycache__", "node_modules"]


def test_oversized_and_null_byte_files_are_excluded(tmp_path):
    (tmp_path / "big.txt").write_text("a" * 200)
    (tmp_path / "blob.dat").write_bytes(b"ab\x00cd")
    inv = _inv(tmp_path, max_file_bytes=100)
    reasons = {e.path: e.excluded for e in inv.files}
    assert reasons["big.txt"] is ExclusionReason.OVERSIZED
    assert reasons["blob.dat"] is ExclusionReason.BINARY


def test_symlinks_are_listed_as_excluded_not_followed(tmp_path, make_symlink):
    outside = tmp_path / "outside"
    root = tmp_path / "repo"
    outside.mkdir()
    root.mkdir()
    (outside / "s.txt").write_text("secret")
    make_symlink(outside, root / "dirlink")
    make_symlink(outside / "s.txt", root / "filelink")
    inv = _inv(root)
    assert {e.path: e.excluded for e in inv.files} == {
        "dirlink": ExclusionReason.SYMLINK,
        "filelink": ExclusionReason.SYMLINK,
    }


def test_file_count_limit(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x")
    with pytest.raises(BudgetExceededError):
        _inv(tmp_path, max_files=3)


def test_total_size_limit(tmp_path):
    (tmp_path / "a.txt").write_text("a" * 100)
    (tmp_path / "b.txt").write_text("b" * 100)
    with pytest.raises(BudgetExceededError):
        _inv(tmp_path, max_total_bytes=150)


def test_non_python_repo_is_treated_like_any_other(tmp_path):
    (tmp_path / "index.js").write_text("console.log(1)")
    (tmp_path / "package.json").write_text("{}")
    inv = _inv(tmp_path)
    assert inv.primary_language == "JavaScript" and inv.ecosystems == ["node"]
    assert inv.notes == []  # nothing says "unsupported"


def _write(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_many_ecosystems_are_detected_with_their_tests_and_entry_points(tmp_path):
    _write(tmp_path, {
        "go/go.mod": "module x\nrequire github.com/gin-gonic/gin v1.9.0\n",
        "go/main.go": "package main", "go/handler_test.go": "package main",
        "java/pom.xml": "<project><dependency>spring-boot</dependency></project>",
        "java/src/main/java/App.java": "class App {}",
        "java/src/test/java/AppTest.java": "class AppTest {}",
        "rust/Cargo.toml": "[dependencies]\naxum = \"0.7\"",
        "rust/src/main.rs": "fn main() {}",
        "dotnet/app.csproj": "<Project/>", "dotnet/Program.cs": "class P {}",
        "dart/pubspec.yaml": "dependencies:\n  flutter:\n", "dart/lib/main.dart": "void main() {}",
        "ruby/Gemfile": "gem 'rails'", "ruby/spec/user_spec.rb": "describe 1",
        "web/package.json": '{"devDependencies": {"jest": "1", "typescript": "5"}}',
        "web/src/index.ts": "export {}", "web/src/app.test.ts": "test(1)",
    })  # fmt: skip
    inv = _inv(tmp_path)
    assert inv.ecosystems == ["dart", "dotnet", "go", "java", "node", "ruby", "rust"]
    assert {"go/handler_test.go", "java/src/test/java/AppTest.java", "ruby/spec/user_spec.rb",
            "web/src/app.test.ts"} <= set(inv.test_files)  # fmt: skip
    assert {"go/main.go", "rust/src/main.rs", "dotnet/Program.cs", "dart/lib/main.dart",
            "web/src/index.ts"} <= set(inv.entry_points)  # fmt: skip
    assert {"gin-gonic/gin", "spring-boot", "axum", "flutter", "rails", "jest",
            "typescript"} <= set(inv.framework_hints)  # fmt: skip


def test_languages_beyond_the_big_five_are_recognised(tmp_path):
    _write(tmp_path, {"a.kt": "fun main() {}", "b.swift": "print(1)", "c.dart": "void main() {}",
                      "d.sql": "select 1;", "e.tf": "resource {}"})  # fmt: skip
    langs = {e.path: e.language for e in _inv(tmp_path).files}
    assert langs == {"a.kt": "Kotlin", "b.swift": "Swift", "c.dart": "Dart",
                     "d.sql": "SQL", "e.tf": "Terraform"}  # fmt: skip


def test_empty_repository_says_so(tmp_path):
    (tmp_path / "notes.bin.dat").write_bytes(b"\x00\x01")
    inv = _inv(tmp_path)
    assert inv.ecosystems == []
    assert inv.notes == ["No recognizable source files or manifests found."]


def test_package_json_frameworks(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "1", "left-pad": "1"}}')
    assert _inv(tmp_path).framework_hints == ["react"]


def test_devops_files_are_detected(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: ci")
    (tmp_path / "Dockerfile").write_text("FROM python:3.12")
    (tmp_path / "vercel.json").write_text("{}")
    (tmp_path / "app.py").write_text("print(1)")
    assert _inv(tmp_path).devops_files == [".github/workflows/ci.yml", "Dockerfile", "vercel.json"]
