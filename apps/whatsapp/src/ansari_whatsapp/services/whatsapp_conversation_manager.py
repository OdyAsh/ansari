# WhatsApp Conversation Manager
"""Service for orchestrating WhatsApp conversations and message handling."""

import asyncio
import time
from datetime import datetime

import httpx
import sentry_sdk
from loguru import logger

from ansari_whatsapp.services.service_provider import get_ansari_client, get_auth_client
from ansari_whatsapp.services.meta_service_provider import get_meta_api_service
from ansari_whatsapp.utils.app_logger import request_id_var, user_id_var
from ansari_whatsapp.utils.exceptions import (
    AuthTokenError,
    ThreadCreationError,
    ThreadInfoError,
    MessageProcessingError,
)
from ansari_whatsapp.utils.token_utils import get_subject
from ansari_whatsapp.utils.time_utils import calculate_time_passed, get_retention_time_seconds
from ansari_whatsapp.presenters.whatsapp_message_formatter import format_for_whatsapp
from ansari_whatsapp.utils.whatsapp_message_splitter import split_message


class WhatsAppConversationManager:
    """Orchestrates WhatsApp conversation workflows.

    This service manages the lifecycle of WhatsApp conversations, coordinating
    between apps/auth, apps/api and the Meta WhatsApp API to handle:
    - User identification by BSUID (exchanged for a JWT; the phone number is never used)
    - Message processing and response generation
    - Typing indicators and user experience features
    - Thread/conversation management
    """

    def __init__(
        self,
        bsuid: str = None,
        incoming_msg_type: str = None,
        incoming_msg_body: dict = None,
        message_id: str = None,
        message_unix_time: int = None,
    ):
        """Initialize the conversation manager with user-specific details.

        Args:
            bsuid (str, optional): The business-scoped user ID of the sender (e.g., "US.13491208655302741918").
            incoming_msg_type (str, optional): The type of incoming message (text, image, etc.).
            incoming_msg_body (dict, optional): The body content of the incoming message.
            message_id (str, optional): The ID of the incoming message.
            message_unix_time (int, optional): The timestamp of the incoming message in Unix format.
        """
        self.bsuid = bsuid
        self.token = None  # The user's JWT, set by authenticate_user()
        self.incoming_msg_type = incoming_msg_type
        self.incoming_msg_body = incoming_msg_body
        self.message_id = message_id
        self.message_unix_time = message_unix_time

        # Initialize services and tracking variables
        self.ansari_client = get_ansari_client()
        self.auth_client = get_auth_client()
        self.meta_api_service = get_meta_api_service()
        self.typing_indicator_task = None
        self.first_indicator_time = None

    async def send_typing_indicator_then_start_loop(self) -> None:
        """Send a typing indicator and start a loop to periodically send more while processing."""
        if not self.bsuid or not self.message_id:
            logger.error("Cannot start typing indicator loop: missing bsuid or message_id")
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

    async def authenticate_user(self) -> str | None:
        """Exchange the sender's BSUID for a JWT with apps/auth and return the user's GUID.

        The same BSUID always maps to the same GUID, and a new BSUID is a new user. apps/auth creates the
        account on first use, so there is no separate registration step. The token is kept on this object
        for the later apps/api calls.

        Returns:
            str | None: The user's GUID (the JWT's `sub`) on success, None on failure.
        """
        if not self.bsuid:
            logger.error("Cannot authenticate user: bsuid is not set")
            return None

        try:
            self.token = await self.auth_client.get_token(self.bsuid)
        except AuthTokenError as e:
            logger.error(f"Failed to get a token for the user: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting a token for the user: {type(e).__name__}")
            return None

        user_id = get_subject(self.token)
        if not user_id:
            logger.error("The token from apps/auth has no readable subject")
            self.token = None
            return None

        logger.debug(f"Authenticated whatsapp user (user_id: {user_id})")
        return user_id

    async def _send_whatsapp_typing_indicator(self) -> None:
        """Send a typing indicator to the WhatsApp recipient."""
        if not self.bsuid or not self.message_id:
            logger.error("Cannot send typing indicator: missing bsuid or message_id")
            return

        try:
            await self.meta_api_service.send_typing_indicator(recipient_phone=self.bsuid, message_id=self.message_id)
        except Exception as e:
            logger.exception(f"Error sending typing indicator: {e}")

    async def send_whatsapp_message(
        self,
        msg_body: str,
        recipient_bsuid: str = None,
    ) -> None:
        """Send a message to the WhatsApp recipient.

        Args:
            msg_body (str): The message body to be sent.
            recipient_bsuid (str, optional): The recipient's BSUID. Defaults to self.bsuid if not provided.
        """
        recipient = recipient_bsuid or self.bsuid

        if not recipient:
            logger.error("Cannot send WhatsApp message: No recipient provided")
            return

        # Split the message if it exceeds WhatsApp's character limit
        message_parts = split_message(msg_body)

        try:
            # Delegate to Meta API service (its `recipient_phone` argument also accepts a BSUID)
            await self.meta_api_service.send_message(recipient_phone=recipient, message_parts=message_parts)
        except Exception as e:
            logger.exception(f"Error sending WhatsApp message: {e}")

    async def handle_text_message(self) -> None:
        """Process an incoming text message and send a response to the WhatsApp sender."""
        if not self.bsuid or not self.token:
            logger.error("Cannot process text message: the user is not authenticated")
            return

        try:
            incoming_txt_msg = self.incoming_msg_body["body"]
            logger.debug(f"Whatsapp user sent a text message ({len(incoming_txt_msg)} characters)")

            # Get details of the thread that the user last interacted with
            try:
                last_thread_info = await self.ansari_client.get_last_thread_info(self.token)
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
                    result = await self.ansari_client.create_thread(self.token, first_few_words)
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
                    token=self.token,
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
            logger.debug(f"Response received for markdown conversion ({len(response)} characters)")
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
        if not self.bsuid or not self.incoming_msg_type:
            logger.error("Cannot process unsupported message: missing bsuid or incoming_msg_type")
            return

        msg_type = self.incoming_msg_type + "s" if not self.incoming_msg_type.endswith("s") else self.incoming_msg_type
        msg_type = msg_type.replace("unsupporteds", "this media type")
        await self.send_whatsapp_message(
            f"Sorry, I can't process {msg_type} yet. Please send me a text message.",
        )
