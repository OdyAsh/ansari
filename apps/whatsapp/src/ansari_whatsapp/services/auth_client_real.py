# Real apps/auth client
"""Real client for apps/auth's `/whatsapp` token exchange, over HTTP."""

import httpx
from loguru import logger

from ansari_whatsapp.services.auth_client_base import AuthClientBase
from ansari_whatsapp.utils.config import get_settings
from ansari_whatsapp.utils.exceptions import AuthTokenError

# Assumed contract (spec 321): POST /api/whatsapp/session, {"bsuid": ...} -> {"token": ...}
SESSION_PATH = "/api/whatsapp/session"
API_KEY_HEADER = "X-Whatsapp-Api-Key"


class AuthClientReal(AuthClientBase):
    """Calls apps/auth over HTTP, authenticated with AUTH_WHATSAPP_API_KEY."""

    def __init__(self):
        settings = get_settings()
        self.url = f"{settings.AUTH_SERVER_URL.rstrip('/')}{SESSION_PATH}"
        self.client = httpx.AsyncClient(
            headers={API_KEY_HEADER: settings.AUTH_WHATSAPP_API_KEY.get_secret_value()},
            # Awaited before Meta gets its reply (Meta wants a response within ~5s), so keep it short
            timeout=5.0,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def get_token(self, bsuid: str) -> str:
        try:
            response = await self.client.post(self.url, json={"bsuid": bsuid})
            response.raise_for_status()
            token = response.json().get("token")
        except httpx.HTTPStatusError as e:
            logger.error(f"apps/auth rejected the token exchange: HTTP {e.response.status_code}")
            raise AuthTokenError(f"Token exchange failed: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error during token exchange: {type(e).__name__}")
            raise AuthTokenError("Network error during token exchange") from e
        except ValueError as e:
            logger.error("apps/auth returned a non-JSON token response")
            raise AuthTokenError("Invalid token response") from e

        if not isinstance(token, str) or not token:
            logger.error("apps/auth token response had no token")
            raise AuthTokenError("Token response had no token")
        return token
