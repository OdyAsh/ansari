# Real API Client for ansari-whatsapp
"""Real client implementation for interacting with the Ansari backend API via HTTP."""

import httpx

from loguru import logger

from ansari_whatsapp.utils.config import get_settings
from ansari_whatsapp.utils.exceptions import (
    ThreadCreationError,
    ThreadDeletionError,
    ThreadInfoError,
    MessageProcessingError,
)
from ansari_whatsapp.services.ansari_client_base import AnsariClientBase

# Per-client attribution id sent to apps/api (lowercase, per packages/types attribution)
CLIENT_ID = "askansari-whatsapp"

# The invisible keep-alive character apps/api sends while the model is still thinking
HEARTBEAT_CHAR = "\u200b"


class AnsariClientReal(AnsariClientBase):
    """Real client for the Ansari backend API that makes actual HTTP requests.

    Uses a persistent httpx.AsyncClient with connection pooling for better performance.
    References:
    - https://www.python-httpx.org/advanced/clients/#client-instances
    - https://www.python-httpx.org/advanced/clients/#why-use-a-client
    """

    def __init__(self):
        """Initialize the Ansari API client with persistent HTTP client and authentication.

        The persistent client provides several benefits:
        - Connection pooling and reuse for improved performance
        - Automatic header injection (authentication, content-type)
        - Single configuration point for all HTTP requests

        References:
        - https://www.python-httpx.org/advanced/#client-instances
        - https://fastapi.tiangolo.com/tutorial/security/
        """
        self.settings = get_settings()
        self.base_url = self.settings.BACKEND_SERVER_URL

        # Persistent HTTP client; the user's JWT is added per request, since each user has their own
        self.client = httpx.AsyncClient(
            headers={
                "X-Ansari-Client": CLIENT_ID,
                "Content-Type": "application/json",
            },
            timeout=60.0,  # Default timeout for all requests
        )

    @staticmethod
    def _auth(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    async def close(self):
        """Close the HTTP client connection and release resources.

        This should be called when the client is no longer needed to properly clean up
        connection pools and background tasks.

        References:
        - https://www.python-httpx.org/advanced/#client-instances
        - https://www.python-httpx.org/async/#opening-and-closing-clients
        """
        await self.client.aclose()

    async def create_thread(self, token: str, title: str) -> dict:
        """
        Create a new thread for the user that this JWT belongs to.

        Args:
            token (str): The user's JWT from apps/auth.
            title (str): The title of the thread.

        Returns:
            dict: The creation result with thread_id.

        Raises:
            ThreadCreationError: If thread creation fails.
        """
        try:
            response = await self.client.post(
                f"{self.base_url}/api/v2/threads",
                json={"name": title, "source": "whatsapp"},
                headers=self._auth(token),
            )
            response.raise_for_status()
            return {"thread_id": response.json()["thread_id"]}
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error creating thread: {e.response.status_code}")
            raise ThreadCreationError(f"Failed to create thread: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error creating thread: {type(e).__name__}")
            raise ThreadCreationError("Network error during thread creation") from e
        except (KeyError, ValueError) as e:
            logger.error("apps/api returned an unexpected thread-creation response")
            raise ThreadCreationError("Unexpected thread-creation response") from e

    async def delete_thread(self, token: str, thread_id: str) -> None:
        """
        Delete a thread and, with it, all of its messages (apps/api cascades the delete).

        A 404 means the thread is already gone, which is the outcome we wanted, so it is not an error.

        Args:
            token (str): The user's JWT from apps/auth.
            thread_id (str): The ID of the thread to delete.

        Raises:
            ThreadDeletionError: If deletion fails.
        """
        try:
            response = await self.client.delete(f"{self.base_url}/api/v2/threads/{thread_id}", headers=self._auth(token))
            if response.status_code == 404:
                return
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error deleting thread: {e.response.status_code}")
            raise ThreadDeletionError(f"Failed to delete thread: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error deleting thread: {type(e).__name__}")
            raise ThreadDeletionError("Network error during thread deletion") from e

    async def get_last_thread_info(self, token: str) -> dict:
        """
        Get information about the user's most recently active thread.

        apps/api lists a user's threads newest-first by `updated_at`, which it bumps on every message.

        Args:
            token (str): The user's JWT from apps/auth.

        Returns:
            dict: The thread info with thread_id and last_message_time (both None if no thread yet).

        Raises:
            ThreadInfoError: If retrieving thread info fails.
        """
        try:
            response = await self.client.get(f"{self.base_url}/api/v2/threads", headers=self._auth(token))
            response.raise_for_status()
            threads = response.json()
            if not threads:
                return {"thread_id": None, "last_message_time": None}
            return {"thread_id": threads[0]["thread_id"], "last_message_time": threads[0]["updated_at"]}
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error getting last thread info: {e.response.status_code}")
            raise ThreadInfoError(f"Failed to get last thread info: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error getting last thread info: {type(e).__name__}")
            raise ThreadInfoError("Network error during thread info retrieval") from e
        except (KeyError, ValueError, TypeError) as e:
            logger.error("apps/api returned an unexpected thread-list response")
            raise ThreadInfoError("Unexpected thread-list response") from e

    async def process_message(self, token: str, thread_id: str, message: str) -> str:
        """
        Send a message to apps/api and collect the full streamed answer.

        Uses `POST /api/v2/threads/{id}`, whose body is the raw answer text. While the model is
        still thinking, apps/api sends invisible zero-width spaces as keep-alives; they are removed.

        Args:
            token (str): The user's JWT from apps/auth.
            thread_id (str): The ID of the thread.
            message (str): The message to process.

        Returns:
            str: The complete response message.

        Raises:
            MessageProcessingError: If message processing fails.
        """
        try:
            url = f"{self.base_url}/api/v2/threads/{thread_id}"
            data = {"content": message}

            # Use stream=True to receive the response as a stream
            async with self.client.stream("POST", url, json=data, headers=self._auth(token)) as response:
                if response.status_code != 200:
                    error_detail = await response.aread()
                    logger.error(f"Error from backend API: {error_detail}")
                    raise MessageProcessingError(f"Backend returned HTTP {response.status_code}: {error_detail}")

                # Accumulate the full response as we receive chunks
                full_response = ""
                async for chunk in response.aiter_text():
                    # Each chunk is a token from the streaming response
                    if chunk:
                        full_response += chunk.replace(HEARTBEAT_CHAR, "")

                if not full_response:
                    logger.warning("Received empty response from backend")
                    return ""

                # # TEMPORARY: Save response to file for testing/mocking purposes
                # # Comment out this block when you don't want to update the sample response file
                # try:
                #     from pathlib import Path

                #     # Create the directory if it doesn't exist
                #     sample_dir = Path.cwd() / "docs" / "sample_backend_responses"
                #     sample_dir.mkdir(parents=True, exist_ok=True)

                #     # Write the response to the file
                #     sample_file = sample_dir / "sample_ansari_llm_response.txt"
                #     with open(sample_file, "w", encoding="utf-8") as f:
                #         f.write(full_response)
                #     logger.info(f"Saved backend response to {sample_file}")
                # except Exception as e:
                #     logger.warning(f"Failed to save sample response: {e}")
                # # END TEMPORARY CODE

                return full_response
        except httpx.TimeoutException as e:
            logger.error(f"Timeout processing message: {e}")
            raise MessageProcessingError("Request timed out while processing message") from e
        except httpx.RequestError as e:
            logger.error(f"Network error processing message: {type(e).__name__}")
            raise MessageProcessingError("Network error during message processing") from e
        except MessageProcessingError:
            # Re-raise our custom exception without wrapping
            raise
        except Exception as e:
            logger.error(f"Unexpected error processing message: {type(e).__name__}")
            raise MessageProcessingError(f"Unexpected error: {type(e).__name__}") from e
