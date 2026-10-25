# WhatsApp Conversation Manager
"""Service for orchestrating WhatsApp conversations and message handling."""

import asyncio
import time
from datetime import datetime

import httpx
import sentry_sdk
from loguru import logger

from ansari_whatsapp.services.service_provider import get_ansari_client
from ansari_whatsapp.services.meta_service_provider import get_meta_api_service
from ansari_whatsapp.utils.app_logger import request_id_var, user_id_var
from ansari_whatsapp.utils.exceptions import (
    UserRegistrationError,
    UserExistsCheckError,
    ThreadCreationError,
    ThreadInfoError,
    MessageProcessingError,
)
from ansari_whatsapp.utils.language_utils import get_language_from_text
from ansari_whatsapp.utils.time_utils import calculate_time_passed, get_retention_time_seconds
from ansari_whatsapp.presenters.whatsapp_message_formatter import format_for_whatsapp
from ansari_whatsapp.utils.whatsapp_message_splitter import split_message


class WhatsAppConversationManager:
    """Orchestrates WhatsApp conversation workflows.

    This service manages the lifecycle of WhatsApp conversations, coordinating
    between the Ansari backend API and Meta WhatsApp API to handle:
    - User registration and authentication
    - Message processing and response generation
    - Typing indicators and user experience features
    - Thread/conversation management
    """

    def __init__(
        self,
        user_phone_num: str = None,
        incoming_msg_type: str = None,
        incoming_msg_body: dict = None,
        message_id: str = None,
        message_unix_time: int = None,
    ):
        """Initialize the conversation manager with user-specific details.

        Args:
            user_phone_num (str, optional): The WhatsApp phone number of the user.
            incoming_msg_type (str, optional): The type of incoming message (text, image, etc.).
            incoming_msg_body (dict, optional): The body content of the incoming message.
            message_id (str, optional): The ID of the incoming message.
            message_unix_time (int, optional): The timestamp of the incoming message in Unix format.
        """
        self.user_phone_num = user_phone_num
        self.incoming_msg_type = incoming_msg_type
        self.incoming_msg_body = incoming_msg_body
        self.message_id = message_id
        self.message_unix_time = message_unix_time

        # Initialize services and tracking variables
        self.ansari_client = get_ansari_client()
        self.meta_api_service = get_meta_api_service()
        self.typing_indicator_task = None
        self.first_indicator_time = None

    async def send_typing_indicator_then_start_loop(self) -> None:
        """Send a typing indicator and start a loop to periodically send more while processing."""
        if not self.user_phone_num or not self.message_id:
            logger.error("Cannot start typing indicator loop: missing user_phone_num or message_id")
            return

        self.first_indicator_time = time.time()

        # Send the initial typing indicator
        await self._send_whatsapp_typing_indicator()

        # Start an async task that will keep sending typing indicators
        self.typing_indicator_task = asyncio.create_task(self._typing_indicator_loop())

    async def _typing_indicator_loop(self) -> None:
        """Loop that periodically sends typing indicators while processing a message."""
        MAX_DURATION_SECONDS = 300  # 5 minutes maximum
        INDICATOR_INTERVAL_SECONDS = 26  # Send indicator every 26 seconds

        try:
            while True:
                logger.debug("Currently in typing indicator loop (i.e., Ansari is taking longer than usual to respond)")
                # Sleep for the interval
                await asyncio.sleep(INDICATOR_INTERVAL_SECONDS)

                # Check if we've exceeded the maximum duration
                elapsed_time = time.time() - self.first_indicator_time
                if elapsed_time > MAX_DURATION_SECONDS:
                    logger.warning(f"Typing indicator loop exceeded maximum duration of {MAX_DURATION_SECONDS}s. Stopping.")
                    break

                # If we're still processing the message, send another typing indicator
                logger.debug(f"Sending follow-up typing indicator after {elapsed_time:.1f}s")
                await self._send_whatsapp_typing_indicator()

        except asyncio.CancelledError:
            logger.debug("Typing indicator task cancelled")
        except Exception as e:
            logger.error(f"Error in typing indicator loop: {e}")
            logger.exception(e)

    async def retrieve_or_register_user(self) -> str | None:
        """Retrieve existing user ID or register new user and return their ID.

        This method attempts to get the user_id for a phone number. If the user
        doesn't exist, it registers them and returns the newly created user_id.

        Returns:
            str | None: The user's database ID (UUID string) on success, None on failure.
        """
        if not self.user_phone_num:
            logger.error("Cannot retrieve or register user: user_phone_num is not set")
            return None

        try:
            # Try to get the user_id from backend
            user_id = await self.ansari_client.check_user_exists(self.user_phone_num)
            logger.debug(f"User exists with user_id: {user_id}")
            return user_id

        except UserExistsCheckError:
            # User doesn't exist - register them with detected language
            logger.debug(f"User {self.user_phone_num} not found, registering new user")

            if self.incoming_msg_type == "text":
                incoming_msg_text = self.incoming_msg_body["body"]
                user_lang = get_language_from_text(incoming_msg_text)
            else:
                # Use English as default language if we can't detect it
                user_lang = "en"

            try:
                result = await self.ansari_client.register_user(self.user_phone_num, user_lang)

                # Extract user_id from registration response
                if isinstance(result, dict):
                    user_id = result.get("user_id")
                    logger.info(f"Registered new whatsapp user (lang: {user_lang}, user_id: {user_id}): {self.user_phone_num}")
                    return user_id

                # Registration succeeded but no user_id returned (shouldn't happen)
                logger.error(f"User registration succeeded but no user_id returned for {self.user_phone_num}")
                return None

            except UserRegistrationError as e:
                logger.exception(f"Failed to register user {self.user_phone_num}: {e}")
                await self.send_whatsapp_message("Sorry, we couldn't register your account. Please try again later.")
                return None

        except Exception as e:
            logger.exception(f"Unexpected error retrieving/registering user: {e}")
            return None

    async def _send_whatsapp_typing_indicator(self) -> None:
        """Send a typing indicator to the WhatsApp recipient."""
        if not self.user_phone_num or not self.message_id:
            logger.error("Cannot send typing indicator: missing user_phone_num or message_id")
            return

        try:
            await self.meta_api_service.send_typing_indicator(recipient_phone=self.user_phone_num, message_id=self.message_id)
        except Exception as e:
            logger.exception(f"Error sending typing indicator: {e}")

    async def send_whatsapp_message(
        self,
        msg_body: str,
        recipient_phone_num: str = None,
    ) -> None:
        """Send a message to the WhatsApp recipient.

        Args:
            msg_body (str): The message body to be sent.
            recipient_phone_num (str, optional): The recipient's WhatsApp number.
                                            Defaults to self.user_phone_num if not provided.
        """
        # Use the provided recipient_phone_num or fall back to self.user_phone_num
        phone_num = recipient_phone_num or self.user_phone_num

        if not phone_num:
            logger.error("Cannot send WhatsApp message: No recipient phone number provided")
            return

        # Split the message if it exceeds WhatsApp's character limit
        message_parts = split_message(msg_body)

        try:
            # Delegate to Meta API service
            await self.meta_api_service.send_message(recipient_phone=phone_num, message_parts=message_parts)
        except Exception as e:
            logger.exception(f"Error sending WhatsApp message: {e}")

    async def handle_text_message(self) -> None:
        """Process an incoming text message and send a response to the WhatsApp sender."""
        if not self.user_phone_num:
            logger.error("Cannot process text message: user_phone_num is not set")
            return

        try:
            incoming_txt_msg = self.incoming_msg_body["body"]
            logger.debug(f"Whatsapp user said: {incoming_txt_msg}")

            # Get details of the thread that the user last interacted with
            try:
                last_thread_info = await self.ansari_client.get_last_thread_info(self.user_phone_num)
                thread_id = last_thread_info.get("thread_id")
                last_msg_time = last_thread_info.get("last_message_time")
            except ThreadInfoError as e:
                logger.error(f"Failed to get thread info: {e}")
                await self.send_whatsapp_message(
                    "Sorry, we're having trouble accessing your chat history. Please try again later."
                )
                return

            if last_msg_time and isinstance(last_msg_time, str):
                last_msg_time = datetime.fromisoformat(last_msg_time.replace("Z", "+00:00"))

            # Calculate the time passed since the last message
            passed_time, passed_time_logging = calculate_time_passed(last_msg_time)
            logger.debug(f"Time passed since user's last whatsapp message: {passed_time_logging}")

            # Get the allowed retention time
            allowed_time = get_retention_time_seconds()

            # Create a new thread if no threads have been previously created,
            # or the last message has passed the allowed retention time
            if thread_id is None or passed_time > allowed_time:
                first_few_words = " ".join(incoming_txt_msg.split()[:6])

                try:
                    result = await self.ansari_client.create_thread(self.user_phone_num, first_few_words)
                    thread_id = result.get("thread_id")
                    logger.info("Created a new thread for the whatsapp user, " + "as the allowed retention time has passed.")
                except ThreadCreationError as e:
                    logger.error(f"Failed to create thread: {e}")
                    await self.send_whatsapp_message(
                        "An unexpected error occurred while creating a new chat session. Please try again later."
                    )
                    return

            # Process the message using the Ansari backend API
            try:
                response = await self.ansari_client.process_message(
                    phone_num=self.user_phone_num,
                    thread_id=thread_id,
                    message=incoming_txt_msg,
                )
            except httpx.TimeoutException as e:
                logger.error(f"Backend timeout while processing message: {e}")
                sentry_sdk.set_tag("error_type", "backend_timeout")
                sentry_sdk.set_context(
                    "message_details",
                    {
                        "request_id": request_id_var.get(),
                        "user_id": user_id_var.get(),
                    },
                )
                sentry_sdk.capture_exception(e)
                await self.send_whatsapp_message("The request timed out. Please try sending your message again.")
                # Cancel typing indicator if running
                if self.typing_indicator_task and not self.typing_indicator_task.done():
                    self.typing_indicator_task.cancel()
                return
            except MessageProcessingError as e:
                logger.error(f"Failed to process message: {e}")
                sentry_sdk.set_tag("error_type", "message_processing_failure")
                sentry_sdk.set_context(
                    "message_details",
                    {
                        "request_id": request_id_var.get(),
                        "user_id": user_id_var.get(),
                    },
                )
                sentry_sdk.capture_exception(e)
                await self.send_whatsapp_message("An error occurred while processing your message. Please try again later.")
                # Cancel typing indicator if running
                if self.typing_indicator_task and not self.typing_indicator_task.done():
                    self.typing_indicator_task.cancel()
                return

            # Cancel the typing indicator task if it's still running
            if self.typing_indicator_task and not self.typing_indicator_task.done():
                logger.debug("Canceling typing indicator task as message processing is complete")
                self.typing_indicator_task.cancel()

            if not response:
                logger.warning("Received an empty response from the backend")
                await self.send_whatsapp_message(
                    "Sorry, we couldn't process your message. Please try again later.",
                )
                return

            # Convert conventional markdown syntax to WhatsApp's markdown syntax
            logger.debug(f"Response before markdown conversion: \n\n{response}")
            response = format_for_whatsapp(response)

            if not response:
                logger.warning("Response was empty after markdown conversion")
                await self.send_whatsapp_message(
                    "Sorry, we couldn't process your message. Please try again later..",
                )
                return

            # Return the response back to the WhatsApp user
            await self.send_whatsapp_message(response)

        except Exception as e:
            logger.exception(f"Unexpected error processing text message: {e}")
            await self.send_whatsapp_message(
                "An unexpected error occurred while processing your message. Please try again later.",
            )
            # Cancel typing indicator if running
            if self.typing_indicator_task and not self.typing_indicator_task.done():
                self.typing_indicator_task.cancel()

    async def handle_unsupported_message(self) -> None:
        """Handle an incoming unsupported message by sending an appropriate response."""
        if not self.user_phone_num or not self.incoming_msg_type:
            logger.error("Cannot process unsupported message: missing user_phone_num or incoming_msg_type")
            return

        msg_type = self.incoming_msg_type + "s" if not self.incoming_msg_type.endswith("s") else self.incoming_msg_type
        msg_type = msg_type.replace("unsupporteds", "this media type")
        await self.send_whatsapp_message(
            f"Sorry, I can't process {msg_type} yet. Please send me a text message.",
        )
