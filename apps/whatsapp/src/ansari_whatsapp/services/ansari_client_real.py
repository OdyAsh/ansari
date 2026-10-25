# Real API Client for ansari-whatsapp
"""Real client implementation for interacting with the Ansari backend API via HTTP."""

import httpx

from loguru import logger

from ansari_whatsapp.utils.config import get_settings
from ansari_whatsapp.utils.exceptions import (
    UserRegistrationError,
    UserExistsCheckError,
    ThreadCreationError,
    ThreadHistoryError,
    ThreadInfoError,
    MessageProcessingError,
)
from ansari_whatsapp.services.ansari_client_base import AnsariClientBase


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

        # Create persistent HTTP client with authentication headers
        # All requests will automatically include these headers
        self.client = httpx.AsyncClient(
            headers={
                "X-Whatsapp-Api-Key": self.settings.WHATSAPP_SERVICE_API_KEY.get_secret_value(),
                "Content-Type": "application/json",
            },
            timeout=60.0,  # Default timeout for all requests
        )

    async def close(self):
        """Close the HTTP client connection and release resources.

        This should be called when the client is no longer needed to properly clean up
        connection pools and background tasks.

        References:
        - https://www.python-httpx.org/advanced/#client-instances
        - https://www.python-httpx.org/async/#opening-and-closing-clients
        """
        await self.client.aclose()

    async def register_user(self, phone_num: str, preferred_language: str) -> dict:
        """
        Register a new WhatsApp user with the Ansari backend.

        Args:
            phone_num (str): The user's WhatsApp phone number.
            preferred_language (str): The user's preferred language.

        Returns:
            dict: The registration result.

        Raises:
            UserRegistrationError: If the registration fails.
        """
        try:
            response = await self.client.post(
                f"{self.base_url}/whatsapp/v2/users/register",
                json={
                    "phone_num": phone_num,
                    "preferred_language": preferred_language,
                },
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error registering user {phone_num}: {e.response.status_code}")
            raise UserRegistrationError(f"Failed to register user: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error registering user {phone_num}: {e}")
            raise UserRegistrationError("Network error during registration") from e

    async def check_user_exists(self, phone_num: str) -> str:
        """
        Get user ID for a WhatsApp phone number.

        Args:
            phone_num (str): The user's WhatsApp phone number.

        Returns:
            str: The user's ID

        Raises:
            UserExistsCheckError: If the user is not found or the check fails.
        """
        try:
            response = await self.client.get(
                f"{self.base_url}/whatsapp/v2/users/exists",
                params={"phone_num": phone_num},
            )
            response.raise_for_status()
            user_id = response.json()
            return user_id
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"User {phone_num} not found (404)")
                raise UserExistsCheckError("User not found") from e
            logger.error(f"HTTP error getting user ID for {phone_num}: {e.response.status_code}")
            raise UserExistsCheckError(f"Failed to get user ID: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error getting user ID for {phone_num}: {e}")
            raise UserExistsCheckError("Network error during user ID retrieval") from e

    async def create_thread(self, phone_num: str, title: str) -> dict:
        """
        Create a new thread for a WhatsApp user in the Ansari backend.

        Args:
            phone_num (str): The user's WhatsApp phone number.
            title (str): The title of the thread.

        Returns:
            dict: The creation result with thread_id.

        Raises:
            ThreadCreationError: If thread creation fails.
        """
        try:
            response = await self.client.post(
                f"{self.base_url}/whatsapp/v2/threads",
                json={"phone_num": phone_num, "title": title},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error creating thread for {phone_num}: {e.response.status_code}")
            raise ThreadCreationError(f"Failed to create thread: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error creating thread for {phone_num}: {e}")
            raise ThreadCreationError("Network error during thread creation") from e

    async def get_thread_history(self, phone_num: str, thread_id: str) -> dict:
        """
        Get the message history for a WhatsApp user's thread from the Ansari backend.

        Args:
            phone_num (str): The user's WhatsApp phone number.
            thread_id (str): The ID of the thread.

        Returns:
            dict: The thread history.

        Raises:
            ThreadHistoryError: If retrieving thread history fails.
        """
        try:
            response = await self.client.get(
                f"{self.base_url}/whatsapp/v2/threads/{thread_id}/history",
                params={"phone_num": phone_num},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error getting thread history for {phone_num}: {e.response.status_code}")
            raise ThreadHistoryError(f"Failed to get thread history: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error getting thread history for {phone_num}: {e}")
            raise ThreadHistoryError("Network error during thread history retrieval") from e

    async def get_last_thread_info(self, phone_num: str) -> dict:
        """
        Get information about the last active thread for a WhatsApp user.

        Args:
            phone_num (str): The user's WhatsApp phone number.

        Returns:
            dict: The thread info with thread_id and last_message_time.

        Raises:
            ThreadInfoError: If retrieving thread info fails.
        """
        try:
            response = await self.client.get(
                f"{self.base_url}/whatsapp/v2/threads/last",
                params={"phone_num": phone_num},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error getting last thread info for {phone_num}: {e.response.status_code}")
            raise ThreadInfoError(f"Failed to get last thread info: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"Network error getting last thread info for {phone_num}: {e}")
            raise ThreadInfoError("Network error during thread info retrieval") from e

    async def process_message(self, phone_num: str, thread_id: str, message: str) -> str:
        """
        Process a message from a WhatsApp user with the Ansari backend.
        This method streams the response from the backend to avoid timeout issues.

        Args:
            phone_num (str): The user's WhatsApp phone number.
            thread_id (str): The ID of the thread.
            message (str): The message to process.

        Returns:
            str: The complete response message.

        Raises:
            MessageProcessingError: If message processing fails.
        """
        try:
            url = f"{self.base_url}/whatsapp/v2/messages/process"
            data = {
                "phone_num": phone_num,
                "thread_id": thread_id,
                "message": message,
            }

            # Use stream=True to receive the response as a stream
            # Note: self.client already has auth headers and default timeout configured
            async with self.client.stream("POST", url, json=data) as response:
                if response.status_code != 200:
                    error_detail = await response.aread()
                    logger.error(f"Error from backend API: {error_detail}")
                    raise MessageProcessingError(f"Backend returned HTTP {response.status_code}: {error_detail}")

                # Accumulate the full response as we receive chunks
                full_response = ""
                async for chunk in response.aiter_text():
                    # Each chunk is a token from the streaming response
                    if chunk:
                        full_response += chunk

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
            logger.error(f"Timeout processing message for {phone_num}: {e}")
            raise MessageProcessingError("Request timed out while processing message") from e
        except httpx.RequestError as e:
            logger.error(f"Network error processing message for {phone_num}: {e}")
            raise MessageProcessingError("Network error during message processing") from e
        except MessageProcessingError:
            # Re-raise our custom exception without wrapping
            raise
        except Exception as e:
            logger.error(f"Unexpected error processing message for {phone_num}: {e}")
            raise MessageProcessingError(f"Unexpected error: {str(e)}") from e
