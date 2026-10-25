# Mock API Client for ansari-whatsapp
"""Mock client implementation that simulates the Ansari backend without making real HTTP requests."""

import random
import asyncio
from datetime import datetime, timezone
from functools import wraps
from typing import Callable

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


def simulate_backend_behavior(
    min_latency_s: int = 1, max_latency_s: int = 2, error_rate: float = 0.0, error_class: type[Exception] | None = None
) -> Callable:
    """Decorator that simulates backend behavior with configurable latency and error rates.

    Args:
        min_latency_s: Minimum simulated network delay in seconds (default: 1)
        max_latency_s: Maximum simulated network delay in seconds (default: 2)
        error_rate: Probability of simulated errors, 0.0-1.0 (default: 0.0, no errors)
        error_class: Specific exception class to raise, or None to auto-detect (default: None)

    Returns:
        Decorator function that wraps the target function with simulated backend behavior

    Example:
        @simulate_backend_behavior()  # Use all defaults
        async def my_function(): ...

        @simulate_backend_behavior(min_latency_s=100, max_latency_s=500, error_rate=0.05)
        async def my_function(): ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Simulate network latency
            delay = random.uniform(min_latency_s, max_latency_s)
            await asyncio.sleep(delay)

            # Simulate errors if configured
            if error_rate > 0 and random.random() < error_rate:
                logger.warning(f"Mock client simulating error for {func.__name__}")

                # Use provided error_class or auto-detect from function name
                exception_class = error_class
                if exception_class is None:
                    # Auto-detect appropriate error class from function name
                    if "register" in func.__name__:
                        exception_class = UserRegistrationError
                    elif "exists" in func.__name__:
                        exception_class = UserExistsCheckError
                    elif "thread" in func.__name__ and "create" in func.__name__:
                        exception_class = ThreadCreationError
                    elif "history" in func.__name__:
                        exception_class = ThreadHistoryError
                    elif "thread" in func.__name__:
                        exception_class = ThreadInfoError
                    elif "message" in func.__name__:
                        exception_class = MessageProcessingError
                    else:
                        exception_class = Exception

                raise exception_class(f"Simulated error in {func.__name__}")

            return await func(*args, **kwargs)

        return wrapper

    return decorator


class AnsariClientMock(AnsariClientBase):
    """Mock implementation of Ansari client for testing without backend.

    This client simulates backend responses with realistic latency and error rates.
    It maintains in-memory state to provide stateful mocking across method calls.
    """

    def __init__(self):
        """Initialize the mock Ansari API client with in-memory state."""
        self.settings = get_settings()
        # In-memory state for mock data
        self._users = {}  # phone_num -> user_data
        self._threads = {}  # thread_id -> thread_data
        self._thread_counter = 0
        self._user_counter = 0

        logger.info("Initialized AnsariClientMock - using mock backend")

    @simulate_backend_behavior()
    async def register_user(self, phone_num: str, preferred_language: str) -> dict:
        """Mock user registration.

        Args:
            phone_num: The user's WhatsApp phone number
            preferred_language: The user's preferred language

        Returns:
            dict: Registration result matching backend format {"status": "success", "user_id": "..."}

        Raises:
            UserRegistrationError: Simulated registration errors (2% chance)
        """
        if phone_num in self._users:
            logger.warning(f"Mock: User {phone_num} already exists")
            raise UserRegistrationError("User already registered")

        self._user_counter += 1
        user_id = f"mock_user_{self._user_counter}"

        self._users[phone_num] = {
            "user_id": user_id,
            "phone_num": phone_num,
            "preferred_language": preferred_language,
        }

        logger.info(f"Mock: Registered user {phone_num} with ID {user_id}")
        return {"status": "success", "user_id": user_id}

    @simulate_backend_behavior()
    async def check_user_exists(self, phone_num: str) -> str:
        """Mock user ID retrieval.

        Args:
            phone_num: The user's WhatsApp phone number

        Returns:
            str: The user's ID

        Raises:
            UserExistsCheckError: If user not found
        """
        if phone_num not in self._users:
            logger.debug(f"Mock: User {phone_num} not found")
            raise UserExistsCheckError("User not found")

        user_id = self._users[phone_num]["user_id"]
        logger.debug(f"Mock: Found user_id for {phone_num}: {user_id}")
        return user_id

    @simulate_backend_behavior()
    async def create_thread(self, phone_num: str, title: str) -> dict:
        """Mock thread creation.

        Args:
            phone_num: The user's WhatsApp phone number
            title: The thread title

        Returns:
            dict: Creation result matching backend format {"thread_id": "..."}

        Raises:
            ThreadCreationError: If user not found or simulated error (2% chance)
        """
        if phone_num not in self._users:
            logger.error(f"Mock: User {phone_num} not found for thread creation")
            raise ThreadCreationError("User not found")

        self._thread_counter += 1
        thread_id = f"mock_thread_{self._thread_counter}"

        self._threads[thread_id] = {
            "thread_id": thread_id,
            "phone_num": phone_num,
            "title": title,
            "messages": [],
            "last_message_time": None,
        }

        logger.info(f"Mock: Created thread {thread_id} for {phone_num}")
        return {"thread_id": thread_id}

    @simulate_backend_behavior()
    async def get_thread_history(self, phone_num: str, thread_id: str) -> dict:
        """Mock thread history retrieval.

        Args:
            phone_num: The user's WhatsApp phone number
            thread_id: The thread ID

        Returns:
            dict: Thread history matching backend format

        Raises:
            ThreadHistoryError: If thread not found or simulated error (1% chance)
        """
        if thread_id not in self._threads:
            logger.error(f"Mock: Thread {thread_id} not found")
            raise ThreadHistoryError("Thread not found")

        thread = self._threads[thread_id]

        if thread["phone_num"] != phone_num:
            logger.error(f"Mock: Thread {thread_id} does not belong to user {phone_num}")
            raise ThreadHistoryError("Thread access denied")

        logger.debug(f"Mock: Retrieved history for thread {thread_id}")
        return {"thread_id": thread_id, "messages": thread["messages"]}

    @simulate_backend_behavior()
    async def get_last_thread_info(self, phone_num: str) -> dict:
        """Mock last thread info retrieval.

        Args:
            phone_num: The user's WhatsApp phone number

        Returns:
            dict: Thread info matching backend format {"thread_id": "...", "last_message_time": "..."}

        Raises:
            ThreadInfoError: Simulated error (1% chance)
        """
        user_threads = [t for t in self._threads.values() if t["phone_num"] == phone_num]

        if not user_threads:
            logger.debug(f"Mock: No threads found for user {phone_num}")
            return {"thread_id": None, "last_message_time": None}

        # Get the thread with the most recent message time (or last created)
        latest_thread = max(user_threads, key=lambda t: t["last_message_time"] if t["last_message_time"] else "")

        logger.debug(f"Mock: Last thread for {phone_num} is {latest_thread['thread_id']}")
        return {
            "thread_id": latest_thread["thread_id"],
            "last_message_time": latest_thread["last_message_time"],
        }

    @simulate_backend_behavior()  # min_latency_s=60, max_latency_s=60
    async def process_message(self, phone_num: str, thread_id: str, message: str) -> str:
        """Mock message processing with simulated AI response.

        Args:
            phone_num: The user's WhatsApp phone number
            thread_id: The thread ID
            message: The user's message

        Returns:
            str: Mock AI response (uses saved sample response if message contains "long")

        Raises:
            MessageProcessingError: If thread not found or simulated error (3% chance)
        """
        if thread_id not in self._threads:
            logger.error(f"Mock: Thread {thread_id} not found for message processing")
            raise MessageProcessingError("Thread not found")

        thread = self._threads[thread_id]

        if thread["phone_num"] != phone_num:
            logger.error(f"Mock: Thread {thread_id} does not belong to user {phone_num}")
            raise MessageProcessingError("Thread access denied")

        # Add user message to thread
        thread["messages"].append({"role": "user", "content": message})

        # Generate mock AI response
        response = f"This is a *mock AI assistant* running in test mode. Write 'long' to see a bigger mock response.\n\nYour message: {message[:100]}"
        # If message contains "long", try to load the saved sample response from ansari-backend
        if "long" in message.lower():
            try:
                from pathlib import Path

                sample_file = (
                    Path(__file__).parent.parent.parent.parent
                    / "docs"
                    / "sample_backend_responses"
                    / "sample_ansari_llm_response.txt"
                )

                if sample_file.exists():
                    with open(sample_file, "r", encoding="utf-8") as f:
                        response = f.read()
                    logger.info(f"Mock: Using saved sample response from {sample_file}")
                else:
                    logger.warning(f"Mock: Sample response file not found at {sample_file}, using default response")
            except Exception as e:
                logger.warning(f"Mock: Failed to load sample response: {e}, using default response")

        # Add AI response to thread
        thread["messages"].append({"role": "assistant", "content": response})

        # Update last message time
        thread["last_message_time"] = datetime.now(timezone.utc).isoformat()

        logger.info(f"Mock: Processed message for thread {thread_id}, response length: {len(response)}")
        return response
