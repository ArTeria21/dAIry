from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol

from itsdangerous import BadSignature, URLSafeSerializer


class InvalidCredentials(PermissionError):
    """Raised when submitted credentials do not match configured credentials."""


class RateLimitExceeded(PermissionError):
    """Raised when too many login attempts were made by one client."""


class PasswordVerifier(Protocol):
    def verify(self, password_hash: str, password: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class AuthSettings:
    username: str
    password_argon2: str
    session_secret: str
    rate_limit_attempts: int = 5
    rate_limit_window_seconds: int = 60


@dataclass(frozen=True, slots=True)
class AuthResult:
    username: str
    session_token: str


class Argon2PasswordVerifier:
    def verify(self, password_hash: str, password: str) -> bool:
        from argon2 import PasswordHasher
        from argon2.exceptions import VerificationError

        try:
            return PasswordHasher().verify(password_hash, password)
        except VerificationError:
            return False


class SessionSigner:
    def __init__(self, secret: str) -> None:
        self.serializer = URLSafeSerializer(secret, salt="dairy-session")

    def sign(self, username: str) -> str:
        return self.serializer.dumps({"username": username, "iat": int(time.time())})

    def unsign(self, token: str) -> str | None:
        try:
            data = self.serializer.loads(token)
        except (BadSignature, json.JSONDecodeError, ValueError):
            return None
        username = data.get("username")
        return username if isinstance(username, str) else None


class LoginRateLimiter:
    def __init__(self, *, attempts: int, window_seconds: int) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}

    def check(self, client_id: str) -> None:
        now = time.monotonic()
        failures = self._active_failures(client_id, now)
        if len(failures) >= self.attempts:
            raise RateLimitExceeded("Too many login attempts")

    def record_failure(self, client_id: str) -> None:
        now = time.monotonic()
        failures = self._active_failures(client_id, now)
        failures.append(now)
        self._failures[client_id] = failures

    def reset(self, client_id: str) -> None:
        self._failures.pop(client_id, None)

    def _active_failures(self, client_id: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        return [
            timestamp
            for timestamp in self._failures.get(client_id, [])
            if timestamp >= cutoff
        ]


class AuthService:
    def __init__(
        self,
        *,
        settings: AuthSettings,
        verifier: PasswordVerifier | None = None,
        signer: SessionSigner | None = None,
        limiter: LoginRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.verifier = verifier or Argon2PasswordVerifier()
        self.signer = signer or SessionSigner(settings.session_secret)
        self.limiter = limiter or LoginRateLimiter(
            attempts=settings.rate_limit_attempts,
            window_seconds=settings.rate_limit_window_seconds,
        )

    def login(self, *, username: str, password: str, client_id: str) -> AuthResult:
        self.limiter.check(client_id)
        if not self._valid_credentials(username=username, password=password):
            self.limiter.record_failure(client_id)
            raise InvalidCredentials("Invalid username or password")
        self.limiter.reset(client_id)
        return AuthResult(
            username=self.settings.username,
            session_token=self.signer.sign(self.settings.username),
        )

    def authenticate(self, session_token: str) -> str | None:
        username = self.signer.unsign(session_token)
        if username != self.settings.username:
            return None
        return username

    def _valid_credentials(self, *, username: str, password: str) -> bool:
        if username != self.settings.username:
            return False
        return self.verifier.verify(self.settings.password_argon2, password)
