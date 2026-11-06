"""AuthenticationProvider: MockAuthenticationProvider (dev login) and GoogleOIDCAuthenticationProvider.

Google sign-in is verified on the BACKEND (signature, audience, issuer, expiry, email_verified, domain).
A frontend-only Google result is never trusted. App login never grants Google Ads access.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from app.core.config import get_settings
from app.core.errors import Unauthorized


class AuthenticationProvider(ABC):
    name = "auth"
    source = "MOCK"

    @abstractmethod
    def verify(self, credential: dict) -> dict:
        """Return {email, name, sub} or raise Unauthorized."""


class MockAuthenticationProvider(AuthenticationProvider):
    name = "MockAuthenticationProvider"
    source = "MOCK"

    def verify(self, credential: dict) -> dict:
        if get_settings().APP_ENV == "production":
            raise Unauthorized("Development login is disabled in production")
        email = (credential.get("email") or "").strip().lower()
        if not email or "@" not in email:
            raise Unauthorized("Email required for development login")
        return {"email": email, "name": credential.get("name") or email.split("@")[0], "sub": None}


class GoogleOIDCAuthenticationProvider(AuthenticationProvider):
    name = "GoogleOIDCAuthenticationProvider"
    source = "LIVE"
    ISSUERS = ("accounts.google.com", "https://accounts.google.com")

    def __init__(self, verifier: Callable[[str, str], dict] | None = None):
        self._verifier = verifier

    def _verify_token(self, token: str, audience: str) -> dict:
        if self._verifier:
            return self._verifier(token, audience)
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token
        return id_token.verify_oauth2_token(token, g_requests.Request(), audience)

    def verify(self, credential: dict) -> dict:
        s = get_settings()
        token = credential.get("id_token") or credential.get("credential")
        if not token:
            raise Unauthorized("Missing Google ID token")
        if not s.GOOGLE_CLIENT_ID:
            raise Unauthorized("Google sign-in is not configured (GOOGLE_CLIENT_ID)")
        try:
            claims = self._verify_token(token, s.GOOGLE_CLIENT_ID)
        except Exception as exc:  # signature / expiry / audience failures
            raise Unauthorized("Google ID token verification failed") from exc
        if claims.get("iss") not in self.ISSUERS:
            raise Unauthorized("Invalid token issuer")
        if claims.get("aud") != s.GOOGLE_CLIENT_ID:
            raise Unauthorized("Invalid token audience")
        if not claims.get("email_verified"):
            raise Unauthorized("Google email is not verified")
        email = claims.get("email", "").lower()
        domains = s.allowed_login_domains
        if domains and email.split("@")[-1] not in domains and (claims.get("hd") or "") not in domains:
            raise Unauthorized("This Google account's domain is not allowed")
        return {"email": email, "name": claims.get("name", ""), "sub": claims.get("sub")}
