# Custom exceptions for ansari-whatsapp
"""Custom exception classes for the Ansari WhatsApp service."""


class UnexpectedMetaPayloadError(Exception):
    """Meta sent a message payload that is missing an expected key."""

    def __init__(self, message: str, reply_to: str | None = None):
        super().__init__(message)
        # Phone number or BSUID we can still use to reach the sender, if the payload had one
        self.reply_to = reply_to


class PhoneHiddenUserError(Exception):
    """Sender's webhook has a BSUID but no phone number (WhatsApp username privacy)."""

    def __init__(self, bsuid: str):
        super().__init__("Message sender did not share a phone number (BSUID only)")
        self.bsuid = bsuid


class AnsariClientError(Exception):
    """Base exception for Ansari client errors."""

    pass


class UserRegistrationError(AnsariClientError):
    """User registration failed."""

    pass


class UserExistsCheckError(AnsariClientError):
    """Checking if user exists failed."""

    pass


class ThreadCreationError(AnsariClientError):
    """Thread creation failed."""

    pass


class ThreadHistoryError(AnsariClientError):
    """Retrieving thread history failed."""

    pass


class ThreadInfoError(AnsariClientError):
    """Retrieving thread info failed."""

    pass


class MessageProcessingError(AnsariClientError):
    """Message processing failed."""

    pass
