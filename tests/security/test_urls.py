import pytest

from ase.core.errors import UnsafeUrlError
from ase.security.urls import canonical_clone_url, parse_github_repo_url


def test_valid_urls_parse():
    assert parse_github_repo_url("https://github.com/octocat/Hello-World") == (
        "octocat",
        "Hello-World",
    )
    assert parse_github_repo_url("https://github.com/octocat/Hello-World.git/") == (
        "octocat",
        "Hello-World",
    )
    assert canonical_clone_url("https://GitHub.com/a/b") == "https://github.com/a/b.git"


def test_malicious_or_unsupported_urls_are_rejected():
    bad = [
        "",
        "http://github.com/a/b",
        "git://github.com/a/b",
        "file:///etc/passwd",
        "ssh://git@github.com/a/b",
        "https://evil.com/github.com/a/b",
        "https://github.com.evil.com/a/b",
        "https://github.com@evil.com/a/b",
        "https://user:pass@github.com/a/b",
        "https://github.com:8443/a/b",
        "https://github.com/a/b?x=1",
        "https://github.com/a/b#frag",
        "https://github.com/a",
        "https://github.com/a/b/c",
        "https://github.com/a/..",
        "https://github.com/-bad/b",
        "https://127.0.0.1/a/b",
        "https://169.254.169.254/latest/meta-data",
        "https://github.com/a/b c",
        "https://github.com\\@evil.com/a/b",
        "https://github.com/" + "a" * 300 + "/b",
    ]
    for url in bad:
        with pytest.raises(UnsafeUrlError):
            parse_github_repo_url(url)
