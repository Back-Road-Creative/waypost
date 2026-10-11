"""
Rate limiting for API calls.

Provides:
- Request per second limiting
- Concurrent request limiting
- Batch operation pacing
"""

import threading
import time
from collections import deque
from typing import Optional


class RateLimiter:
    """
    Rate limiter for API calls.

    Features:
    - Configurable requests per second
    - Automatic delay calculation
    - Thread-safe operations
    - Context manager support
    - Request tracking
    """

    def __init__(
        self, requests_per_second: float = 10.0, max_concurrent: Optional[int] = None
    ):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Maximum requests per second
            max_concurrent: Maximum concurrent requests (optional)
        """
        self.requests_per_second = requests_per_second
        self.max_concurrent = max_concurrent
        self.request_times = deque(maxlen=100)  # Keep last 100 timestamps
        self.lock = threading.Lock()
        self.semaphore = threading.Semaphore(max_concurrent) if max_concurrent else None
        self.last_request_time = None

    def wait_if_needed(self):
        """
        Wait if necessary to maintain rate limit.

        This method will block until it's safe to make another request
        according to the configured rate limit.
        """
        with self.lock:
            current_time = time.monotonic()

            if self.last_request_time is not None:
                # Calculate required delay
                min_interval = 1.0 / self.requests_per_second
                elapsed = current_time - self.last_request_time
                wait_time = min_interval - elapsed

                if wait_time > 0:
                    time.sleep(wait_time)
                    current_time = time.monotonic()

            # Record this request
            self.last_request_time = current_time
            self.request_times.append(current_time)

    def acquire(self):
        """
        Acquire a slot for concurrent requests.

        Only applicable if max_concurrent was set.
        """
        if self.semaphore:
            self.semaphore.acquire()
        self.wait_if_needed()

    def release(self):
        """
        Release a concurrent request slot.

        Only applicable if max_concurrent was set.
        """
        if self.semaphore:
            self.semaphore.release()

    def reset(self):
        """Reset rate limiter state (clear all tracking)."""
        with self.lock:
            self.request_times.clear()
            self.last_request_time = None

    def __enter__(self):
        """Enter context manager."""
        self.wait_if_needed()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager."""
        # Nothing to clean up
        return False
