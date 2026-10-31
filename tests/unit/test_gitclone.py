import os
from pathlib import Path

from ase.repository.gitclone import build_clone_command, clean_git_env


def test_clone_command_is_hardened():
    cmd = build_clone_command("git", "https://github.com/a/b.git", Path("/ws/x"))
    assert cmd[0] == "git"
    assert "protocol.allow=never" in cmd and "protocol.https.allow=always" in cmd
    assert "core.symlinks=false" in cmd
    assert "--depth" in cmd and "--single-branch" in cmd and "--no-tags" in cmd
    # the URL must come after "--" so it can never be parsed as an option
    assert cmd.index("--") < cmd.index("https://github.com/a/b.git")
    assert "--recurse-submodules" not in cmd


def test_revocation_check_flag_is_only_added_when_disabled():
    url, dest = "https://github.com/a/b.git", Path("/ws/x")
    assert "http.schannelCheckRevoke=false" not in build_clone_command("git", url, dest)
    off = build_clone_command("git", url, dest, check_revocation=False)
    assert "http.schannelCheckRevoke=false" in off
    assert off.index("clone") > off.index("http.schannelCheckRevoke=false")  # option before 'clone'
    assert "protocol.allow=never" in off  # other hardening still applies


def test_git_env_does_not_leak_secrets():
    planted = {
        "GITHUB_TOKEN": "x", "ASE_LLM_API_KEY": "y", "AWS_SECRET_ACCESS_KEY": "z",
        "OPENAI_API_KEY": "w",
    }  # fmt: skip
    old = {k: os.environ.get(k) for k in planted}
    os.environ.update(planted)
    try:
        env = clean_git_env()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert not set(planted) & set(env)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
