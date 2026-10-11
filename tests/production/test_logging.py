"""
Test suite for structured logging.

Following TDD principles - these tests should FAIL until implementation is complete.
"""

import json
import os
import tempfile
from io import StringIO
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestStructuredLogger:
    """Test StructuredLogger class."""

    def test_logger_initialization(self):
        """Test logger initialization with default settings."""
        from seo_manager.production.structured_logger import StructuredLogger

        logger = StructuredLogger(name="test")
        assert logger.name == "test"

    def test_logger_with_file_output(self):
        """Test logger with file output."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            logger.info("test message")

            with open(log_file) as f:
                content = f.read()
                assert "test message" in content
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_json_format_output(self):
        """Test that logs are in JSON format."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            logger.info("test message", extra_field="value")

            with open(log_file) as f:
                line = f.readline().strip()
                log_data = json.loads(line)
                assert log_data["event"] == "test message"
                assert log_data["extra_field"] == "value"
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_log_levels(self):
        """Test different log levels (ERROR, WARNING, INFO)."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            logger.error("error message")
            logger.warning("warning message")
            logger.info("info message")

            with open(log_file) as f:
                lines = f.readlines()
                assert len(lines) == 3

                error_log = json.loads(lines[0])
                assert error_log["level"] == "error"
                assert error_log["event"] == "error message"

                warning_log = json.loads(lines[1])
                assert warning_log["level"] == "warning"

                info_log = json.loads(lines[2])
                assert info_log["level"] == "info"
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)


class TestSecretSanitization:
    """Test secret sanitization from logs."""

    def test_sanitize_token(self):
        """Test that API tokens are sanitized."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            logger.info("API call", token="secret_token_12345")

            with open(log_file) as f:
                content = f.read()
                assert "secret_token_12345" not in content
                assert "***REDACTED***" in content or "REDACTED" in content
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_sanitize_password(self):
        """Test that passwords are sanitized."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            # fmt: off
            logger.info("Login attempt", password="my_secret_password")  # pragma: allowlist secret
            # fmt: on

            with open(log_file) as f:
                content = f.read()
                assert "my_secret_password" not in content
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_sanitize_api_key(self):
        """Test that API keys are sanitized."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            # fmt: off
            logger.info("API request", api_key="AIza1234567890")  # pragma: allowlist secret
            # fmt: on

            with open(log_file) as f:
                content = f.read()
                assert "AIza1234567890" not in content
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_sanitize_client_secret(self):
        """Test that client secrets are sanitized."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            # fmt: off
            logger.info("OAuth", client_secret="GOCSPX-abcdefgh")  # pragma: allowlist secret
            # fmt: on

            with open(log_file) as f:
                content = f.read()
                assert "GOCSPX-abcdefgh" not in content
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_sanitize_nested_secrets(self):
        """Test sanitization of secrets in nested structures."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            logger.info(
                "Complex data",
                credentials={
                    "token": "secret123",
                    "user": "john",
                    "password": "pass456",  # pragma: allowlist secret
                },
            )

            with open(log_file) as f:
                content = f.read()
                assert "secret123" not in content
                assert "pass456" not in content
                assert "john" in content  # Non-secret should remain
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)


class TestContextualLogging:
    """Test contextual logging with metadata."""

    def test_log_with_brand_id(self):
        """Test logging with brand_id context."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            logger.info("Upload", brand_id="examplebrand")

            with open(log_file) as f:
                log_data = json.loads(f.readline())
                assert log_data["brand_id"] == "examplebrand"
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_log_with_operation_context(self):
        """Test logging with operation context."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            logger.info("Process started", operation="youtube_upload")

            with open(log_file) as f:
                log_data = json.loads(f.readline())
                assert log_data["operation"] == "youtube_upload"
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_log_with_duration(self):
        """Test logging with duration/timing information."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            logger.info("Operation complete", duration_ms=1234.5)

            with open(log_file) as f:
                log_data = json.loads(f.readline())
                assert log_data["duration_ms"] == 1234.5
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_log_with_timestamp(self):
        """Test that logs include timestamps."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            logger.info("Test")

            with open(log_file) as f:
                log_data = json.loads(f.readline())
                assert "timestamp" in log_data
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)


class TestPerformanceLogging:
    """Test performance metrics logging."""

    def test_log_api_call_duration(self):
        """Test logging API call duration."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            logger.info("API call", endpoint="videos.list", duration_ms=250)

            with open(log_file) as f:
                log_data = json.loads(f.readline())
                assert log_data["duration_ms"] == 250
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_log_quota_usage(self):
        """Test logging quota usage."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            logger.info("Quota check", quota_used=5000, quota_limit=10000)

            with open(log_file) as f:
                log_data = json.loads(f.readline())
                assert log_data["quota_used"] == 5000
                assert log_data["quota_limit"] == 10000
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)


class TestMultipleOutputStreams:
    """Test logging to multiple output streams."""

    def test_log_to_console_and_file(self):
        """Test logging to both console and file."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(
                name="test", log_file=log_file, console_output=True
            )
            logger.info("Test message")

            # Check file
            with open(log_file) as f:
                content = f.read()
                assert "Test message" in content
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_log_file_only(self):
        """Test logging to file only (no console)."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(
                name="test", log_file=log_file, console_output=False
            )
            logger.info("File only message")

            with open(log_file) as f:
                content = f.read()
                assert "File only message" in content
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)


class TestLoggerIntegration:
    """Test logger integration features."""

    def test_bind_context(self):
        """Test binding context to logger."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)
            bound_logger = logger.bind(brand_id="test_brand")
            bound_logger.info("Test message")

            with open(log_file) as f:
                log_data = json.loads(f.readline())
                assert log_data["brand_id"] == "test_brand"
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_exception_logging(self):
        """Test logging exceptions with stack traces."""
        from seo_manager.production.structured_logger import StructuredLogger

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        try:
            logger = StructuredLogger(name="test", log_file=log_file)

            try:
                raise ValueError("Test error")
            except Exception:
                logger.error("Error occurred", exc_info=True)

            with open(log_file) as f:
                content = f.read()
                assert "Error occurred" in content
                assert "ValueError" in content or "exception" in content.lower()
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)
