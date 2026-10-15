"""Private PLAT credentials; Octop identities remain independent of organizations."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from octop.infra.auth.sso.crypto import decrypt_secret, encrypt_secret
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.sso import SsoProviderRow


@dataclass(frozen=True)
class PlatCredential:
    access_token: str = field(repr=False)
    expires_at: int
    issuer: str
    subject: str
    client_id: str

    @classmethod
    def from_response(
        cls, token: dict[str, Any], provider: SsoProviderRow, subject: str, requested_at: int
    ) -> PlatCredential:
        access = token.get("access_token")
        lifetime = token.get("expires_in")
        if (
            not isinstance(access, str)
            or not access.strip()
            or any(c in access for c in "\r\n")
            or str(token.get("token_type", "")).lower() != "bearer"
            or isinstance(lifetime, bool)
            or not isinstance(lifetime, int)
            or lifetime <= 0
        ):
            raise ValueError("invalid PLAT token response")
        return cls(access, requested_at + lifetime, provider.issuer, subject, provider.client_id)


class PlatCredentialStore:
    def __init__(self, secrets: SecretRepo) -> None:
        self._secrets = secrets

    @staticmethod
    def _key(user_id: int, provider_id: int) -> str:
        return f"plat_credential:{user_id}:{provider_id}"

    def save(self, user_id: int, provider_id: int, credential: PlatCredential) -> None:
        payload = json.dumps(
            {
                "access_token": credential.access_token,
                "expires_at": credential.expires_at,
                "issuer": credential.issuer,
                "subject": credential.subject,
                "client_id": credential.client_id,
            }
        )
        self._secrets.put(self._key(user_id, provider_id), encrypt_secret(self._secrets, payload))

    def get(self, user_id: int, provider: SsoProviderRow, subject: str) -> PlatCredential | None:
        value = self._secrets.get(self._key(user_id, provider.id))
        if value is None:
            return None
        data = json.loads(decrypt_secret(self._secrets, value))
        credential = PlatCredential(**data)
        if (
            credential.issuer != provider.issuer
            or credential.subject != subject
            or credential.client_id != provider.client_id
            or credential.expires_at <= time.time()
        ):
            return None
        return credential

    def delete(self, user_id: int, provider_id: int) -> None:
        self._secrets.delete(self._key(user_id, provider_id))
