from ase.security.redaction import REDACTED, is_sensitive_path, redact


def test_known_token_formats_are_redacted():
    samples = [
        "ghp_" + "a" * 36,
        "github_pat_" + "A1b2C3d4E5f6G7h8I9j0K1",
        "sk-" + "x" * 32,
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "Bearer " + "abcdefghijklmnop1234",
    ]
    for secret in samples:
        out = redact(f"token is {secret} here")
        assert secret not in out
        assert REDACTED in out


def test_private_key_block_is_redacted():
    text = "a\n-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\nb"
    out = redact(text)
    assert "MIIabc" not in out
    assert out.startswith("a") and out.endswith("b")


def test_env_style_assignments_are_redacted():
    out = redact("API_KEY=hunter2\nexport DB_PASSWORD: s3cret\nDEBUG=true")
    assert "hunter2" not in out
    assert "s3cret" not in out
    assert "DEBUG=true" in out


def test_clean_text_is_unchanged():
    text = "def add(a, b):\n    return a + b\n"
    assert redact(text) == text


def test_sensitive_paths():
    for p in (".env", "app/.env", ".env.production", "keys/server.pem", "id_rsa", ".npmrc"):
        assert is_sensitive_path(p), p
    for p in (".env.example", "config/.env.sample", "src/main.py", "README.md"):
        assert not is_sensitive_path(p), p
