"""Deterministic repository inventory. No network, no model, never executes repo code."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath

from ase.core.errors import BudgetExceededError
from ase.core.models import ExclusionReason, InventoryEntry, RepositoryInventory
from ase.security.redaction import is_sensitive_path

SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", "target", "obj", ".gradle",
    ".dart_tool", "Pods", ".terraform", ".idea",
})  # fmt: skip

LANGUAGES: dict[str, str] = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".cs": "C#", ".fs": "F#", ".vb": "Visual Basic",
    ".swift": "Swift", ".m": "Objective-C", ".mm": "Objective-C", ".dart": "Dart",
    ".lua": "Lua", ".pl": "Perl", ".r": "R", ".jl": "Julia", ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang", ".hs": "Haskell", ".clj": "Clojure",
    ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell",
    ".sql": "SQL", ".vue": "Vue", ".svelte": "Svelte", ".sol": "Solidity", ".tf": "Terraform",
    ".html": "HTML", ".css": "CSS", ".scss": "CSS",
}  # fmt: skip
_MARKUP = frozenset({"HTML", "CSS"})

BINARY_EXT = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".class", ".jar", ".pyc", ".woff", ".woff2", ".ttf",
    ".mp3", ".mp4", ".mov", ".sqlite", ".db",
})  # fmt: skip

# Manifest file name (lower case) -> build ecosystem.
MANIFESTS: dict[str, str] = {
    "requirements.txt": "python", "pyproject.toml": "python", "setup.py": "python",
    "setup.cfg": "python", "pipfile": "python",
    "package.json": "node",
    "pom.xml": "java", "build.gradle": "java", "build.gradle.kts": "java",
    "settings.gradle": "java", "settings.gradle.kts": "java", "build.sbt": "scala",
    "cargo.toml": "rust", "go.mod": "go", "gemfile": "ruby", "composer.json": "php",
    "pubspec.yaml": "dart", "package.swift": "swift", "mix.exs": "elixir",
    "cmakelists.txt": "cmake",
}  # fmt: skip
_DOTNET_SUFFIXES = (".csproj", ".fsproj", ".vbproj")


def manifest_ecosystem(file_name: str) -> str | None:
    name = file_name.lower()
    if name in MANIFESTS:
        return MANIFESTS[name]
    if name.startswith("requirements") and name.endswith(".txt"):
        return "python"
    if name.endswith(_DOTNET_SUFFIXES):
        return "dotnet"
    return None


ENTRY_POINT_NAMES = frozenset({
    "main.py", "app.py", "manage.py", "__main__.py", "wsgi.py", "asgi.py",
    "index.js", "main.js", "server.js", "app.js", "index.ts", "main.ts", "app.ts", "server.ts",
    "main.go", "main.rs", "lib.rs", "program.cs", "main.java", "application.java",
    "main.kt", "application.kt", "main.dart", "main.swift", "main.c", "main.cpp", "main.rb",
    "config.ru", "index.php", "artisan", "main.scala", "main.ex",
})  # fmt: skip
_DEVOPS_NAMES = frozenset({
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "procfile",
    "makefile", "vercel.json", "render.yaml", "netlify.toml", "fly.toml", "app.yaml",
    "serverless.yml", "tox.ini", "pytest.ini", ".dockerignore", ".env.example", "runtime.txt",
    "nixpacks.toml", ".gitlab-ci.yml", "modal_app.py", "jenkinsfile", "azure-pipelines.yml",
    "bitbucket-pipelines.yml", "buildspec.yml", "cloudbuild.yaml",
})  # fmt: skip
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec"})
_TEST_SUFFIXES_LOWER = (
    "_test.py", ".test.js", ".test.ts", ".test.jsx", ".test.tsx", ".spec.js", ".spec.ts",
    ".spec.jsx", ".spec.tsx", "_test.go", "_test.dart", "_spec.rb", "_test.rs",
)  # fmt: skip
_TEST_SUFFIXES_CASED = (
    "Test.java", "Tests.java", "Test.kt", "Tests.kt", "Test.cs", "Tests.cs", "Test.php",
    "Spec.scala", "Test.scala",
)  # fmt: skip
_HINT_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(
        r"\b(django|flask|fastapi|streamlit|pytest|numpy|pandas|sqlalchemy)\b", re.I
    ),
    "java": re.compile(r"\b(spring-boot|spring|quarkus|micronaut|junit|hibernate)\b", re.I),
    "ruby": re.compile(r"\b(rails|sinatra|rspec)\b", re.I),
    "php": re.compile(r"\b(laravel|symfony|phpunit)\b", re.I),
    "go": re.compile(r"(gin-gonic/gin|labstack/echo|gofiber/fiber|gorilla/mux)", re.I),
    "rust": re.compile(r"\b(actix-web|rocket|axum|tokio|serde)\b", re.I),
    "dart": re.compile(r"\b(flutter|shelf)\b", re.I),
    "dotnet": re.compile(r"(Microsoft\.AspNetCore|EntityFramework|xunit|NUnit)", re.I),
}
_JS_FRAMEWORKS = frozenset({
    "react", "next", "vue", "express", "svelte", "angular", "@angular/core", "@nestjs/core",
    "fastify", "nuxt", "vite", "jest", "vitest", "typescript",
})  # fmt: skip
_MAX_DEP_FILE_BYTES = 100_000


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(4096)
    except OSError:
        return True  # unreadable: treat as not safe to show


def _is_devops_path(rel: str) -> bool:
    p = PurePosixPath(rel)
    name = p.name.lower()
    return (
        name in _DEVOPS_NAMES
        or name.startswith("dockerfile")
        or rel.startswith(".github/workflows/")
    )


def _is_test_path(rel: str) -> bool:
    p = PurePosixPath(rel)
    return (
        any(part in _TEST_DIRS for part in p.parts[:-1])
        or p.name.startswith("test_")
        or p.name.lower().endswith(_TEST_SUFFIXES_LOWER)
        or p.name.endswith(_TEST_SUFFIXES_CASED)
    )


def _framework_hints(root: Path, dep_files: list[str]) -> list[str]:
    hints: set[str] = set()
    for rel in dep_files:
        path = root / rel
        name = PurePosixPath(rel).name
        ecosystem = manifest_ecosystem(name)
        try:
            if path.stat().st_size > _MAX_DEP_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if ecosystem == "node" and name.lower() == "package.json":
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if isinstance(data, dict):
                for key in ("dependencies", "devDependencies"):
                    deps = data.get(key)
                    if isinstance(deps, dict):
                        hints.update(d for d in deps if d in _JS_FRAMEWORKS)
        elif ecosystem in _HINT_PATTERNS:
            hints.update(m.lower() for m in _HINT_PATTERNS[ecosystem].findall(text))
    return sorted(hints)


def build_inventory(
    root: Path | str, *, max_files: int, max_total_bytes: int, max_file_bytes: int
) -> RepositoryInventory:
    """Scan ``root``. Raises ``BudgetExceededError`` if the repo is too large."""
    root = Path(root).resolve(strict=True)
    entries: list[InventoryEntry] = []
    skipped: list[str] = []
    total_bytes = 0
    count = 0

    def add(rel: str, size: int, excluded: ExclusionReason | None) -> None:
        language = LANGUAGES.get(PurePosixPath(rel).suffix.lower())
        entries.append(
            InventoryEntry(path=rel, size_bytes=size, language=language, excluded=excluded)
        )

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        rel_dir = base.relative_to(root)
        keep: list[str] = []
        for d in sorted(dirnames):
            rel = (rel_dir / d).as_posix()
            if d in SKIP_DIRS:
                if d != ".git":
                    skipped.append(rel)
            elif (base / d).is_symlink():
                count += 1
                add(rel, 0, ExclusionReason.SYMLINK)
            else:
                keep.append(d)
        dirnames[:] = keep

        for name in sorted(filenames):
            count += 1
            if count > max_files:
                raise BudgetExceededError(f"Repository has more than {max_files} files.")
            full = base / name
            rel = (rel_dir / name).as_posix()
            if full.is_symlink():
                add(rel, 0, ExclusionReason.SYMLINK)
                continue
            try:
                size = full.lstat().st_size
            except OSError:
                add(rel, 0, ExclusionReason.BINARY)
                continue
            total_bytes += size
            if total_bytes > max_total_bytes:
                raise BudgetExceededError(f"Repository is larger than {max_total_bytes} bytes.")
            excluded: ExclusionReason | None = None
            if is_sensitive_path(rel):
                excluded = ExclusionReason.SENSITIVE
            elif size > max_file_bytes:
                excluded = ExclusionReason.OVERSIZED
            elif PurePosixPath(rel).suffix.lower() in BINARY_EXT or _looks_binary(full):
                excluded = ExclusionReason.BINARY
            add(rel, size, excluded)

    entries.sort(key=lambda e: e.path)
    usable = [e for e in entries if e.excluded is None]

    language_bytes: dict[str, int] = {}
    for e in usable:
        if e.language:
            language_bytes[e.language] = language_bytes.get(e.language, 0) + e.size_bytes
    code_langs = {k: v for k, v in language_bytes.items() if k not in _MARKUP}
    ranked = code_langs or language_bytes
    primary = max(ranked, key=lambda k: ranked[k]) if ranked else None

    dep_files = [e.path for e in usable if manifest_ecosystem(PurePosixPath(e.path).name)]
    ecosystems = sorted({manifest_ecosystem(PurePosixPath(p).name) or "" for p in dep_files} - {""})
    excluded_counts: dict[str, int] = {}
    for e in entries:
        if e.excluded:
            excluded_counts[e.excluded.value] = excluded_counts.get(e.excluded.value, 0) + 1

    notes = [] if (primary or dep_files) else ["No recognizable source files or manifests found."]
    return RepositoryInventory(
        files=entries,
        total_files=len(entries),
        total_bytes=total_bytes,
        language_bytes=language_bytes,
        primary_language=primary,
        dependency_files=dep_files,
        test_files=[e.path for e in usable if _is_test_path(e.path)],
        devops_files=[e.path for e in usable if _is_devops_path(e.path)],
        entry_points=[
            e.path for e in usable if PurePosixPath(e.path).name.lower() in ENTRY_POINT_NAMES
        ],
        framework_hints=_framework_hints(root, dep_files),
        skipped_directories=skipped,
        excluded_counts=excluded_counts,
        ecosystems=ecosystems,
        notes=notes,
    )
