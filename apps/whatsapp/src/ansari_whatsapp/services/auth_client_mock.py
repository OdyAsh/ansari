# Mock apps/auth client
"""Mock client that issues fake, unsigned tokens without calling apps/auth."""

import base64
import json
import uuid

from ansari_whatsapp.services.auth_client_base import AuthClientBase
from ansari_whatsapp.utils.exceptions import AuthTokenError


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


class AuthClientMock(AuthClientBase):
    """Maps each BSUID to a stable fake GUID, so one BSUID is one user and a new BSUID is a new user."""

    async def get_token(self, bsuid: str) -> str:
        if not bsuid:
            raise AuthTokenError("Missing BSUID")
        guid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"mock-bsuid:{bsuid}"))
        return f"{_b64({'alg': 'none'})}.{_b64({'sub': guid})}.mock"
