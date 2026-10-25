# Abstract base class for Ansari clients
"""Defines the interface that all Ansari client implementations must follow."""

from abc import ABC, abstractmethod


class AnsariClientBase(ABC):
    """Abstract base class defining the interface for Ansari clients.

    This interface ensures that both real and mock implementations
    provide the same methods with consistent signatures.
    """

    @abstractmethod
    async def register_user(self, phone_num: str, preferred_language: str) -> dict:
        """Register a new WhatsApp user with the Ansari backend.

        Args:
            phone_num (str): The user's WhatsApp phone number.
            preferred_language (str): The user's preferred language.

        Returns:
            dict: The registration result with format {"status": "success", "user_id": "..."}

        Raises:
            UserRegistrationError: If the registration fails.
        """
        pass

    @abstractmethod
    async def check_user_exists(self, phone_num: str) -> str:
        """Get user ID for a WhatsApp phone number.

        Args:
            phone_num (str): The user's WhatsApp phone number.

        Returns:
            str: The user's ID

        Raises:
            UserExistsCheckError: If the user is not found or the check fails.
        """
        pass

    @abstractmethod
    async def create_thread(self, phone_num: str, title: str) -> dict:
        """Create a new thread for a WhatsApp user in the Ansari backend.

        Args:
            phone_num (str): The user's WhatsApp phone number.
            title (str): The title of the thread.

        Returns:
            dict: The creation result with format {"thread_id": "..."}

        Raises:
            ThreadCreationError: If thread creation fails.
        """
        pass

    @abstractmethod
    async def get_thread_history(self, phone_num: str, thread_id: str) -> dict:
        """Get the message history for a WhatsApp user's thread from the Ansari backend.

        Args:
            phone_num (str): The user's WhatsApp phone number.
            thread_id (str): The ID of the thread.

        Returns:
            dict: The thread history with messages.

        Raises:
            ThreadHistoryError: If retrieving thread history fails.
        """
        pass

    @abstractmethod
    async def get_last_thread_info(self, phone_num: str) -> dict:
        """Get information about the last active thread for a WhatsApp user.

        Args:
            phone_num (str): The user's WhatsApp phone number.

        Returns:
            dict: The thread info with format {"thread_id": "...", "last_message_time": "..."}

        Raises:
            ThreadInfoError: If retrieving thread info fails.
        """
        pass

    @abstractmethod
    async def process_message(self, phone_num: str, thread_id: str, message: str) -> str:
        """Process a message from a WhatsApp user with the Ansari backend.

        Args:
            phone_num (str): The user's WhatsApp phone number.
            thread_id (str): The ID of the thread.
            message (str): The message to process.

        Returns:
            str: The complete response message.

        Raises:
            MessageProcessingError: If message processing fails.
        """
        pass
