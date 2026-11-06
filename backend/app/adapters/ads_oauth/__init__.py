"""Google Ads account authorization (separate from app login).

The refresh token is encrypted at rest (Fernet) and only a fingerprint is ever displayed.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from abc import ABC, abstractmethod
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from app.adapters.base import AuthError
from app.core.config import get_settings

ADS_SCOPE = "https://www.googleapis.com/auth/adwords"


def _fernet() -> Fernet:
    s = get_settings()
    key = s.TOKEN_ENCRYPTION_KEY
    if not key:
        if s.APP_ENV == "production":
            raise RuntimeError("TOKEN_ENCRYPTION_KEY is required in production")
        key = base64.urlsafe_b64encode(hashlib.sha256(("token-key|" + s.APP_SECRET_KEY).encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


class AdsOAuthAdapter(ABC):
    name = "ads_oauth"
    source = "MOCK"

    @abstractmethod
    def authorization_url(self, state: str, code_challenge: str) -> str: ...

    @abstractmethod
    def exchange_code(self, code: str, code_verifier: str) -> dict:
        """Return {refresh_token, scopes}."""

    @abstractmethod
    def revoke(self, refresh_token: str) -> None: ...


def new_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


class MockAdsOAuthAdapter(AdsOAuthAdapter):
    name = "MockAdsOAuthAdapter"
    source = "MOCK"

    def authorization_url(self, state, code_challenge):
        return f"/api/v1/ads-oauth/callback?{urlencode({'state': state, 'code': 'mock-authorization-code'})}"

    def exchange_code(self, code, code_verifier):
        if code != "mock-authorization-code":
            raise AuthError("Invalid mock authorization code", adapter=self.name)
        return {"refresh_token": "mock-refresh-" + secrets.token_hex(8), "scopes": [ADS_SCOPE]}

    def revoke(self, refresh_token):
        return None


class GoogleAdsOAuthAdapter(AdsOAuthAdapter):
    name = "GoogleAdsOAuthAdapter"
    source = "LIVE"
    AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN = "https://oauth2.googleapis.com/token"
    REVOKE = "https://oauth2.googleapis.com/revoke"

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=20)

    def authorization_url(self, state, code_challenge):
        s = get_settings()
        if not (s.GOOGLE_ADS_CLIENT_ID and s.GOOGLE_ADS_REDIRECT_URI):
            raise AuthError("Google Ads OAuth client is not configured", adapter=self.name)
        return self.AUTH + "?" + urlencode({
            "client_id": s.GOOGLE_ADS_CLIENT_ID, "redirect_uri": s.GOOGLE_ADS_REDIRECT_URI, "response_type": "code",
            "scope": ADS_SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": code_challenge, "code_challenge_method": "S256", "include_granted_scopes": "true"})

    def exchange_code(self, code, code_verifier):
        s = get_settings()
        resp = self.client.post(self.TOKEN, data={
            "code": code, "client_id": s.GOOGLE_ADS_CLIENT_ID, "client_secret": s.GOOGLE_ADS_CLIENT_SECRET,
            "redirect_uri": s.GOOGLE_ADS_REDIRECT_URI, "grant_type": "authorization_code", "code_verifier": code_verifier})
        if resp.status_code != 200:
            raise AuthError(f"Token exchange failed ({resp.status_code})", adapter=self.name)
        data = resp.json()
        if not data.get("refresh_token"):
            raise AuthError("No refresh token returned (re-consent with prompt=consent)", adapter=self.name)
        return {"refresh_token": data["refresh_token"], "scopes": data.get("scope", "").split()}

    def revoke(self, refresh_token):
        self.client.post(self.REVOKE, data={"token": refresh_token})
