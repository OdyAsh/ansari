# JWT helpers for ansari-whatsapp
"""Read-only helpers for the JWTs that apps/auth issues."""

import base64
import json


def get_subject(token: str) -> str | None:
    """Return the `sub` claim (the user's GUID) of a JWT, or None if it can't be read.

    The signature is NOT verified here; apps/api verifies it on every call. This is only
    used to tag logs with the user's GUID.
    """
    try:
        payload_part = token.split(".")[1]
        payload_part += "=" * (-len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part))
        sub = payload.get("sub")
        return sub if isinstance(sub, str) else None
    except (IndexError, ValueError, AttributeError, TypeError):
        return None
