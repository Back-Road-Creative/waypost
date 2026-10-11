"""
Structured logging with JSON format and secret sanitization.

Provides production-ready logging with:
- JSON structured format
- Secret sanitization
- Multiple output streams
- Contextual logging
- Performance tracking
"""

import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

import structlog


class SecretSanitizer:
    """Sanitize secrets from log data."""

    def __init__(self):
        """Initialize sanitizer with secret patterns."""
        self.secret_keys = {
            "token",
            "password",
            "api_key",
            "apikey",
            "secret",
            "client_secret",
            "access_token",
            "refresh_token",
            "private_key",
            "key",
            "auth",
            "authorization",
        }

    def sanitize(self, data: Any) -> Any:
        """
        Recursively sanitize secrets from data structures.

        Args:
            data: Data to sanitize

        Returns:
            Sanitized data
        """
        if isinstance(data, dict):
            return {
                key: "***REDACTED***"
                if self._is_secret_key(key)
                else self.sanitize(value)
                for key, value in data.items()
            }
        elif isinstance(data, (list, tuple)):
            return [self.sanitize(item) for item in data]
        elif isinstance(data, str):
            return self._sanitize_string(data)
        else:
            return data

    def _is_secret_key(self, key: str) -> bool:
        """Check if a key name indicates a secret."""
        key_lower = key.lower()
        return any(secret in key_lower for secret in self.secret_keys)

    def _sanitize_string(self, value: str) -> str:
        """Sanitize potential secrets in string values."""
        # Common secret patterns
        patterns = [
            (r"AIza[0-9A-Za-z_-]{35}", "***REDACTED_API_KEY***"),  # Google API keys
            (r"GOCSPX-[0-9A-Za-z_-]+", "***REDACTED_CLIENT_SECRET***"),  # OAuth secrets
            (r"ya29\.[0-9A-Za-z_-]+", "***REDACTED_TOKEN***"),  # OAuth tokens
        ]

        sanitized = value
        for pattern, replacement in patterns:
            sanitized = re.sub(pattern, replacement, sanitized)

        return sanitized


def add_sanitizer(logger, method_name, event_dict):
    """Structlog processor to sanitize secrets."""
    sanitizer = SecretSanitizer()
    return sanitizer.sanitize(event_dict)


class StructuredLogger:
    """
    Structured logger with JSON format and secret sanitization.

    Features:
    - JSON structured logging
    - Automatic secret sanitization
    - Multiple output streams (console, file)
    - Contextual logging
    - Performance metrics
    """

    def __init__(
        self,
        name: str,
        log_file: Optional[str] = None,
        console_output: bool = True,
        log_level: str = "INFO",
    ):
        """
        Initialize structured logger.

        Args:
            name: Logger name
            log_file: Optional path to log file
            console_output: Enable console output
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        """
        self.name = name
        self.log_file = log_file
        self.console_output = console_output

        # Configure standard library logging
        self._setup_stdlib_logging(log_level)

        # Configure structlog
        processors = [
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            add_sanitizer,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]

        structlog.configure(
            processors=processors,
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

        self.logger = structlog.get_logger(name)

    def _setup_stdlib_logging(self, log_level: str):
        """Setup standard library logging handlers."""
        # Get numeric log level
        numeric_level = getattr(logging, log_level.upper(), logging.INFO)

        # Create logger
        stdlib_logger = logging.getLogger(self.name)
        stdlib_logger.setLevel(numeric_level)
        stdlib_logger.handlers = []  # Clear existing handlers

        # Console handler
        if self.console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(numeric_level)
            stdlib_logger.addHandler(console_handler)

        # File handler
        if self.log_file:
            # Create directory if needed
            log_path = Path(self.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(self.log_file)
            file_handler.setLevel(numeric_level)
            stdlib_logger.addHandler(file_handler)

    def bind(self, **kwargs) -> "BoundStructuredLogger":
        """
        Bind context to logger.

        Args:
            **kwargs: Context to bind

        Returns:
            BoundStructuredLogger with context
        """
        bound_logger = self.logger.bind(**kwargs)
        return BoundStructuredLogger(bound_logger)

    def info(self, message: str, **kwargs):
        """
        Log info message.

        Args:
            message: Log message
            **kwargs: Additional context
        """
        self.logger.info(message, **kwargs)

    def warning(self, message: str, **kwargs):
        """
        Log warning message.

        Args:
            message: Log message
            **kwargs: Additional context
        """
        self.logger.warning(message, **kwargs)

    def error(self, message: str, exc_info: bool = False, **kwargs):
        """
        Log error message.

        Args:
            message: Log message
            exc_info: Include exception info
            **kwargs: Additional context
        """
        if exc_info:
            self.logger.exception(message, **kwargs)
        else:
            self.logger.error(message, **kwargs)

    def debug(self, message: str, **kwargs):
        """
        Log debug message.

        Args:
            message: Log message
            **kwargs: Additional context
        """
        self.logger.debug(message, **kwargs)


class BoundStructuredLogger:
    """Logger with bound context."""

    def __init__(self, bound_logger):
        """
        Initialize bound logger.

        Args:
            bound_logger: Structlog bound logger
        """
        self.logger = bound_logger

    def info(self, message: str, **kwargs):
        """Log info message with bound context."""
        self.logger.info(message, **kwargs)

    def warning(self, message: str, **kwargs):
        """Log warning message with bound context."""
        self.logger.warning(message, **kwargs)

    def error(self, message: str, exc_info: bool = False, **kwargs):
        """Log error message with bound context."""
        if exc_info:
            self.logger.exception(message, **kwargs)
        else:
            self.logger.error(message, **kwargs)

    def debug(self, message: str, **kwargs):
        """Log debug message with bound context."""
        self.logger.debug(message, **kwargs)
