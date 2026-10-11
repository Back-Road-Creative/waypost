"""
Error recovery and retry logic for production systems.

Provides:
- RetryManager: Exponential backoff retry logic
- CircuitBreaker: Circuit breaker pattern for API calls
- ErrorClassifier: Classify errors as transient or permanent
- Custom exceptions for specific error scenarios
"""

import contextlib
import json
import socket
import time
from typing import Any, Callable, Optional

from googleapiclient.errors import HttpError


# Custom exceptions
class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""

    pass


class QuotaExceededError(Exception):
    """Raised when API quota is exceeded."""

    pass


class DiskFullError(Exception):
    """Raised when disk is full."""

    pass


class ErrorClassifier:
    """Classify errors as transient (retriable) or permanent."""

    def __init__(self):
        """Initialize error classifier."""
        self.transient_http_codes = {429, 500, 502, 503, 504}
        self.permanent_http_codes = {400, 401, 403, 404}

    def is_transient(self, error: Exception) -> bool:
        """
        Determine if an error is transient (retriable).

        Args:
            error: The exception to classify

        Returns:
            True if the error is transient, False if permanent
        """
        # Network timeouts are transient
        if isinstance(error, socket.timeout):
            return True

        # Check HTTP errors
        if isinstance(error, HttpError):
            status_code = error.resp.status

            # Check for quota exceeded specifically
            if status_code == 403:
                try:
                    error_details = json.loads(error.content.decode("utf-8"))
                    if "error" in error_details:
                        errors = error_details["error"].get("errors", [])
                        for err in errors:
                            if err.get("reason") == "quotaExceeded":
                                return False  # Quota errors are not transient
                except:  # noqa: E722
                    pass

            # Transient HTTP codes
            if status_code in self.transient_http_codes:
                return True

            # Permanent HTTP codes
            if status_code in self.permanent_http_codes:
                return False

        # Permission errors are permanent
        if isinstance(error, PermissionError):
            return False

        # OSError with errno 28 is disk full
        if isinstance(error, OSError) and error.errno == 28:  # noqa: SIM103
            return False

        # Default to transient for unknown errors
        return True


class RetryManager:
    """
    Manage retry logic with exponential backoff.

    Features:
    - Configurable max attempts
    - Exponential backoff with max wait time
    - Error classification
    - State preservation across retries
    - Cleanup function support
    """

    def __init__(
        self,
        max_attempts: int = 3,
        initial_wait: float = 1.0,
        max_wait: float = 60.0,
        backoff_multiplier: float = 2.0,
    ):
        """
        Initialize retry manager.

        Args:
            max_attempts: Maximum number of retry attempts
            initial_wait: Initial wait time in seconds
            max_wait: Maximum wait time in seconds
            backoff_multiplier: Multiplier for exponential backoff
        """
        self.max_attempts = max_attempts
        self.initial_wait = initial_wait
        self.max_wait = max_wait
        self.backoff_multiplier = backoff_multiplier
        self.classifier = ErrorClassifier()

    def _calculate_wait_time(self, attempt: int) -> float:
        """
        Calculate wait time for retry attempt using exponential backoff.

        Args:
            attempt: Current attempt number (0-indexed)

        Returns:
            Wait time in seconds
        """
        wait = self.initial_wait * (self.backoff_multiplier**attempt)
        return min(wait, self.max_wait)

    def execute_with_retry(
        self, func: Callable, cleanup_fn: Optional[Callable] = None, *args, **kwargs
    ) -> Any:
        """
        Execute a function with retry logic.

        Args:
            func: Function to execute
            cleanup_fn: Optional cleanup function to call on failure
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result of func

        Raises:
            Exception: If max retries exceeded or permanent error encountered
        """
        last_error = None

        for attempt in range(self.max_attempts):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e

                # Check for quota exceeded
                if isinstance(e, HttpError) and e.resp.status == 403:
                    try:
                        error_details = json.loads(e.content.decode("utf-8"))
                        if "error" in error_details:
                            errors = error_details["error"].get("errors", [])
                            for err in errors:
                                if err.get("reason") == "quotaExceeded":
                                    raise QuotaExceededError(
                                        "API quota exceeded"
                                    ) from e
                    except QuotaExceededError:
                        raise
                    except:  # noqa: E722
                        pass

                # Check for disk full
                if isinstance(e, OSError) and e.errno == 28:
                    raise DiskFullError("No space left on device") from e

                # Check if error is transient
                if not self.classifier.is_transient(e):
                    # Permanent error, don't retry
                    if cleanup_fn:
                        with contextlib.suppress(BaseException):
                            cleanup_fn()
                    raise

                # Don't retry if this was the last attempt
                if attempt == self.max_attempts - 1:
                    if cleanup_fn:
                        with contextlib.suppress(BaseException):
                            cleanup_fn()
                    raise

                # Wait before retry
                wait_time = self._calculate_wait_time(attempt)
                time.sleep(wait_time)

        # Should never reach here, but just in case
        if cleanup_fn:
            with contextlib.suppress(BaseException):
                cleanup_fn()
        raise last_error


class CircuitBreaker:
    """
    Circuit breaker pattern for API calls.

    States:
    - closed: Normal operation
    - open: Rejecting all calls
    - half_open: Testing if service recovered
    """

    def __init__(self, failure_threshold: int = 5, timeout: float = 60.0):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            timeout: Time in seconds before attempting recovery (half-open)
        """
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self._state = "closed"
        self.last_failure_time = None

    @property
    def state(self):
        """Get current state, checking for timeout if open."""
        if self._state == "open" and self._should_attempt_reset():
            return "half_open"
        return self._state

    @state.setter
    def state(self, value):
        """Set the state."""
        self._state = value

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return False
        elapsed = time.monotonic() - self.last_failure_time
        if elapsed >= self.timeout:
            self.state = "half_open"
            return True
        return False

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker.

        Args:
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Result of func

        Raises:
            CircuitBreakerOpenError: If circuit is open
            Exception: Any exception from func
        """
        # Check if we should transition from open to half-open
        current_state = self.state
        if current_state == "half_open":
            # Update internal state to half_open
            self._state = "half_open"

        # Reject if circuit is open (but not half-open)
        if self._state == "open" and not self._should_attempt_reset():
            raise CircuitBreakerOpenError("Circuit breaker is open")

        try:
            result = func(*args, **kwargs)

            # Success - reset if in half-open state
            if self._state == "half_open":
                self._state = "closed"
                self.failure_count = 0

            return result

        except Exception:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()

            # Open circuit if threshold reached
            if self.failure_count >= self.failure_threshold:
                self._state = "open"

            raise


class AtomicOperation:
    """
    Execute operations atomically with rollback support.
    """

    def __init__(self, rollback_fn: Optional[Callable] = None):
        """
        Initialize atomic operation.

        Args:
            rollback_fn: Function to call on failure to rollback changes
        """
        self.rollback_fn = rollback_fn

    def execute(self, operation: Callable, *args, **kwargs) -> Any:
        """
        Execute operation atomically.

        Args:
            operation: Operation to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Result of operation

        Raises:
            Exception: Any exception from operation (after rollback)
        """
        try:
            return operation(*args, **kwargs)
        except Exception:
            # Rollback on failure
            if self.rollback_fn:
                try:  # noqa: SIM105
                    self.rollback_fn()
                except:  # noqa: E722
                    pass  # Ignore rollback errors
            raise
