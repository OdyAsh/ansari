# WhatsApp Logger for ansari-whatsapp
"""Logging configuration for the WhatsApp service using Loguru.

Technical Details:
- Module Execution: Importing this file multiple times will execute its code only once.
  Python modules are singletons. Therefore, it's fine to import this module in any other
    module that needs logging functionality.
  Proof: https://docs.python.org/3/tutorial/modules.html#more-on-modules

- Loguru Instance: The `logger` object is a singleton. Once configured here, the same
  instance is used throughout the application wherever `from ansari_whatsapp.utils.app_logger import logger` is used.
  Proof: https://github.com/Delgan/loguru/issues/54#issuecomment-461724397

- Side Effects on Import: This module configures the global logger immediately upon import.
  This is a deliberate design choice to ensure consistent logging across the application,
  including during tests. Developers should be aware that simply importing this module
  will modify the global logging state.
"""

import contextvars
import json
import os
import sys

from loguru import logger

from ansari_whatsapp.utils.config import get_settings

########################################## Context Variables ##########################################

# Context variables for log correlation
# These automatically propagate to child async tasks (including FastAPI's BackgroundTasks)
# Check docs/logging/understanding_contextvars_in_python.md for details
request_id_var = contextvars.ContextVar("request_id", default="none")
user_id_var = contextvars.ContextVar("user_id", default="none")

########################################## Global Vars and Logger Configurations ##########################################

# Create logs directory if it doesn't exist
os.makedirs("logs", exist_ok=True)

# Remove any pre-existing default handlers made by loguru
logger.remove()

# Get settings
settings = get_settings()

# Determine if we should use JSON structured logging for AWS CloudWatch
# CloudWatch Logs Insights works best with JSON-formatted logs
is_aws_deployment = settings.DEPLOYMENT_TYPE not in ["local", "development"] and os.getenv("GITHUB_ACTIONS") != "true"

########################################## Functions ##########################################


def log_record_preprocessor(record):
    """Preprocess log records before formatting: inject context and apply filters.

    This function runs before Loguru formats the log record and sends it to sink, and it serves two purposes:
    1. Inject request_id and user_id from contextvars into record["extra"]
    2. Filter logs based on LOG_TEST_FILES_ONLY setting

    Args:
        record: The log record being processed.

    Returns:
        bool: True to keep the log, False to discard it.
    """
    # Step 1: Inject context from contextvars
    record["extra"]["request_id"] = request_id_var.get()
    record["extra"]["user_id"] = user_id_var.get()

    # Step 2: Apply filtering logic
    if settings.LOG_TEST_FILES_ONLY and not ("tests" in record["file"].path or "test_" in record["file"].name):
        return False

    return True


def cloudwatch_json_sink(message):
    """Custom sink that writes clean JSON logs for AWS CloudWatch.

    Extracts the record from the message and formats it as minimal JSON.
    Includes request_id and user_id from Loguru's context for log correlation.

    Args:
        message: The loguru message object with .record attribute

    Log Structure:
        - level, text, file_path, line, function, process, thread, time
        - request_id: UUID per HTTP request (for granular debugging)
        - user_id: Database UUID per user (for tracking user conversations)
        - exception: Stack trace details if present

    Note: `level` and `text` keys are mentioned first, as we would like them to visible when
    viewing condensed logs in CloudWatch Console.
    """
    record = message.record
    msg = record["message"]

    log_entry = {
        "level": record["level"].name,
        "text": msg,
        "request_id": record["extra"].get("request_id"),
        "user_id": record["extra"].get("user_id"),
        "file_path": record["file"].path,  # full file path
        "line": record["line"],  # line number
        "function": record["function"],  # function name
        "process": {"id": record["process"].id, "name": record["process"].name},
        "thread": {"id": record["thread"].id, "name": record["thread"].name},
        "time": {"timestamp": record["time"].timestamp(), "iso": record["time"].isoformat()},
    }

    if record["exception"]:
        exc = record["exception"]
        log_entry["exception"] = {
            "type": exc.type.__name__ if exc.type else None,
            "value": str(exc.value) if exc.value else None,
        }

    # Convert to JSON and write to stderr with newline
    # The \n is REQUIRED for newline-delimited JSON (NDJSON) format
    # CloudWatch expects each log entry on a separate line for proper parsing (hence, the `+ "\n"` at the end).
    # See: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Generation_CloudWatch_Agent.html#CloudWatch_Embedded_Metric_Format_Generation_CloudWatch_Agent_Send_Logs
    # json.dumps() serializes the object to a single line. CloudWatch automatically parses this JSON
    # and displays it as an expandable, multi-line structure in the AWS Console GUI.
    sys.stderr.write(json.dumps(log_entry) + "\n")
    sys.stderr.flush()


########################################## Main Code ##########################################

# Choose sink and format based on deployment type
if is_aws_deployment:
    # Use custom sink for AWS CloudWatch
    # This creates clean, minimal JSON logs that are easy to query in CloudWatch Logs Insights
    log_sink = cloudwatch_json_sink

    # Format string for loguru - optimization for custom sink
    # Explanation: Loguru always formats the message string before passing it to the sink.
    # Since our sink uses the raw .record object and handles its own formatting, we use
    # a minimal format "{message}" to avoid the overhead of the default verbose formatter.
    # Source: https://loguru.readthedocs.io/en/stable/api/logger.html#loguru.logger.add:~:text=passed%20to%20all%20added%20sinks%20is%20nothing%20more%20than%20a%20string%20of%20the%20formatted%20log
    log_format = "{message}"
    enable_colors = False
else:
    # Standard stderr sink for local development
    log_sink = sys.stderr
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <4}</level> | "
        "<cyan>{file}</cyan>:<cyan>{line}</cyan> "
        "<blue>[{function}()]</blue> | "
        "<level>{message}</level> | "
        # NOTE: Whenever you want to test/visualize request_id/user_id that appear in CloudWatch,
        # you can uncomment the below line to see them in local logs as well.
        # "req:{extra[request_id]} user:{extra[user_id]}"
    )
    enable_colors = True

# Add console handler for terminal output
logger.add(
    log_sink,
    format=log_format,
    level=settings.LOGGING_LEVEL.upper(),
    enqueue=True,
    colorize=enable_colors,
    backtrace=False,
    diagnose=False,
    filter=log_record_preprocessor,
    catch=False,
)

# # If needed, uncomment below to write logs to all_logs.log file when running locally
# if settings.DEPLOYMENT_TYPE == "local":
#     log_dir = os.path.join(os.getcwd(), "logs")
#     os.makedirs(log_dir, exist_ok=True)
#     all_logs_file = os.path.join(log_dir, "all_logs.log")
#     logger.add(
#         all_logs_file,
#         format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {file}:{line} [{function}()] | {message}",
#         level=settings.LOGGING_LEVEL.upper(),
#         enqueue=True,
#         backtrace=False,
#         diagnose=False,
#         filter=log_record_preprocessor,
#         rotation="10 MB",
#         catch=False,
#     )
