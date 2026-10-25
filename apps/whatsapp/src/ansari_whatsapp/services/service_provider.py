# Service Provider for ansari-whatsapp
"""Factory function for providing the appropriate Ansari client implementation."""

from typing import Optional
from loguru import logger

from ansari_whatsapp.utils.config import get_settings
from ansari_whatsapp.services.ansari_client_base import AnsariClientBase
from ansari_whatsapp.services.ansari_client_real import AnsariClientReal
from ansari_whatsapp.services.ansari_client_mock import AnsariClientMock


# Singleton instance - shared across the entire application
_ansari_client_instance: Optional[AnsariClientBase] = None


def get_ansari_client() -> AnsariClientBase:
    """Factory function that returns THE SAME Ansari client instance (singleton).

    This function implements the Singleton Service Provider pattern, ensuring only one
    client instance exists across the application. This enables:
    - Proper resource management (single connection pool)
    - Efficient cleanup during application shutdown
    - Consistent client state across all requests

    The singleton can be one of:
    - AnsariClientReal: Makes actual HTTP calls to the backend (production/integration testing)
    - AnsariClientMock: Simulates backend responses without network calls (unit testing/offline dev)

    The choice is controlled by the MOCK_ANSARI_CLIENT environment variable, evaluated
    only on first call (subsequent calls return the existing instance).

    Returns:
        AnsariClientBase: The singleton instance (AnsariClientReal or AnsariClientMock)

    Example:
        >>> client = get_ansari_client()  # Creates singleton
        >>> result = await client.register_user("+1234567890", "en")
        >>> client2 = get_ansari_client()  # Returns same instance
        >>> assert client is client2  # True

    Note:
        For test isolation, use reset_ansari_client() to clear the singleton between tests.
    """
    global _ansari_client_instance

    if _ansari_client_instance is None:
        settings = get_settings()

        if settings.MOCK_ANSARI_CLIENT:
            logger.info("Service Provider: Creating AnsariClientMock singleton")
            _ansari_client_instance = AnsariClientMock()
        else:
            logger.debug("Service Provider: Creating AnsariClientReal singleton")
            _ansari_client_instance = AnsariClientReal()

    return _ansari_client_instance


def reset_ansari_client() -> None:
    """Reset the Ansari client singleton instance.

    This function clears the singleton instance, allowing a fresh client to be created
    on the next call to get_ansari_client(). This is primarily used for test isolation
    to ensure each test gets a fresh client instance.

    Usage in tests:
        >>> @pytest.fixture(autouse=True)
        >>> def reset_client():
        >>>     reset_ansari_client()  # Reset before each test
        >>>     yield

    Warning:
        This should NOT be called in production code. It's intended only for test fixtures
        to ensure proper test isolation.
    """
    global _ansari_client_instance
    _ansari_client_instance = None
    logger.debug("Service Provider: Ansari client singleton reset")
