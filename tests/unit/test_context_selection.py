from ase.repository.context import select_context_files
from ase.repository.inventory import build_inventory


def _select(root, **over):
    inv = build_inventory(root, max_files=1000, max_total_bytes=10**7, max_file_bytes=10**6)
    args = dict(max_files=50, max_total_bytes=500_000)
    args.update(over)
    return [path for path, _ in select_context_files(root, inv, **args)]


def _write(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_build_deploy_and_ci_files_reach_the_model(tmp_path):
    _write(tmp_path, {
        "Dockerfile": "FROM node:20", ".github/workflows/ci.yml": "name: ci",
        "vercel.json": "{}", "package.json": "{}", "src/index.ts": "export {}",
        ".env.example": "KEY=", "tsconfig.json": "{}",
    })  # fmt: skip
    sent = _select(tmp_path)
    for expected in ("Dockerfile", ".github/workflows/ci.yml", "vercel.json", "package.json",
                     "src/index.ts", ".env.example", "tsconfig.json"):  # fmt: skip
        assert expected in sent, expected


def test_lockfiles_minified_bundles_secrets_and_binaries_are_not_sent(tmp_path):
    _write(tmp_path, {
        "package-lock.json": "{}", "yarn.lock": "x", "dist-site/app.min.js": "var a;",
        "app.js.map": "{}", ".env": "SECRET=1", "server.pem": "k", "keep.go": "package main",
    })  # fmt: skip
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00")
    assert _select(tmp_path) == ["keep.go"]


def test_most_important_files_come_first_when_the_limit_is_small(tmp_path):
    _write(tmp_path, {
        "main.go": "package main", "go.mod": "module x", "util.go": "package main",
        "README.md": "docs", "Dockerfile": "FROM golang",
    })  # fmt: skip
    sent = _select(tmp_path, max_files=3)
    assert sent[0] == "main.go"  # entry point first
    assert set(sent) == {"main.go", "go.mod", "Dockerfile"}  # then manifest and build files
    # README.md and util.go lose out to them when space is limited
