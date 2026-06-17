from __future__ import annotations

import pytest

from dairy_web.auth import (
    AuthResult,
    AuthService,
    AuthSettings,
    InvalidCredentials,
    RateLimitExceeded,
    SessionSigner,
)


class FakeVerifier:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def verify(self, password_hash: str, password: str) -> bool:
        self.calls.append((password_hash, password))
        return password_hash == "argon2-hash" and password == "correct horse"


def settings() -> AuthSettings:
    return AuthSettings(
        username="artem",
        password_argon2="argon2-hash",
        session_secret="unit-test-secret",
        rate_limit_attempts=3,
        rate_limit_window_seconds=60,
    )


def test_AC_5_auth_accepts_configured_username_password_hash_and_signs_session():
    verifier = FakeVerifier()
    service = AuthService(
        settings=settings(),
        verifier=verifier,
        signer=SessionSigner("unit-test-secret"),
    )

    result = service.login(
        username="artem",
        password="correct horse",
        client_id="127.0.0.1",
    )

    assert isinstance(result, AuthResult)
    assert result.username == "artem"
    assert result.session_token != "correct horse"
    assert service.authenticate(result.session_token) == "artem"
    assert verifier.calls == [("argon2-hash", "correct horse")]


def test_AC_5_auth_rejects_tampered_or_unknown_session_tokens():
    service = AuthService(
        settings=settings(),
        verifier=FakeVerifier(),
        signer=SessionSigner("unit-test-secret"),
    )
    result = service.login(
        username="artem",
        password="correct horse",
        client_id="127.0.0.1",
    )

    assert service.authenticate(result.session_token + "tampered") is None
    assert service.authenticate("") is None


def test_AC_5_login_rate_limits_repeated_failures_per_client():
    service = AuthService(
        settings=settings(),
        verifier=FakeVerifier(),
        signer=SessionSigner("unit-test-secret"),
    )

    for _ in range(3):
        with pytest.raises(InvalidCredentials):
            service.login(
                username="artem",
                password="wrong",
                client_id="127.0.0.1",
            )

    with pytest.raises(RateLimitExceeded):
        service.login(
            username="artem",
            password="correct horse",
            client_id="127.0.0.1",
        )
