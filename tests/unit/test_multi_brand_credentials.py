"""
Unit tests for multi-brand credentials management.

Following TDD: Tests written FIRST, then implementation.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from seo_manager.security.brand_credentials import BrandCredentials


class TestMultiBrandCredentials:
    """Test suite for multi-brand credentials management."""

    @pytest.fixture
    def temp_creds_dir(self, tmp_path):
        """Create a temporary credentials directory."""
        creds_dir = tmp_path / "credentials"
        creds_dir.mkdir()
        return creds_dir

    @pytest.fixture
    def sample_youtube_creds(self):
        """Sample YouTube OAuth credentials."""
        return {
            "token": "ya29.sample_access_token",
            "refresh_token": "1//sample_refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "sample_client_id.apps.googleusercontent.com",
            "client_secret": "sample_client_secret",  # pragma: allowlist secret
            "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
        }

    @pytest.fixture
    def sample_client_secret(self):
        """Sample OAuth client configuration."""
        return {
            "installed": {
                "client_id": "sample_client_id.apps.googleusercontent.com",
                "project_id": "sample-project",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_secret": "sample_client_secret",  # pragma: allowlist secret
            }
        }

    def test_create_brand_credentials(self, temp_creds_dir, sample_youtube_creds):
        """Test creating credentials for a new brand."""
        brand = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )
        brand.save_youtube_credentials(sample_youtube_creds)

        # Verify brand directory created
        brand_dir = temp_creds_dir / "examplebrand"
        assert brand_dir.exists()

        # Verify encrypted file created
        cred_file = brand_dir / "youtube_oauth.json.enc"
        assert cred_file.exists()

    def test_load_brand_credentials(self, temp_creds_dir, sample_youtube_creds):
        """Test loading specific brand's credentials."""
        brand = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )
        brand.save_youtube_credentials(sample_youtube_creds)

        # Load credentials
        loaded_creds = brand.load_youtube_credentials()

        # Verify credentials match
        assert loaded_creds == sample_youtube_creds
        assert loaded_creds["token"] == sample_youtube_creds["token"]
        assert loaded_creds["refresh_token"] == sample_youtube_creds["refresh_token"]

    def test_brand_isolation(self, temp_creds_dir, sample_youtube_creds):
        """Test Brand A cannot access Brand B credentials."""
        # Create credentials for Brand A
        brand_a = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )
        brand_a_creds = sample_youtube_creds.copy()
        brand_a_creds["token"] = "brand_a_token"
        brand_a.save_youtube_credentials(brand_a_creds)

        # Create credentials for Brand B
        brand_b = BrandCredentials(
            brand_id="examplepodcast", credentials_dir=temp_creds_dir
        )
        brand_b_creds = sample_youtube_creds.copy()
        brand_b_creds["token"] = "brand_b_token"
        brand_b.save_youtube_credentials(brand_b_creds)

        # Verify Brand A loads its own credentials
        loaded_a = brand_a.load_youtube_credentials()
        assert loaded_a["token"] == "brand_a_token"

        # Verify Brand B loads its own credentials
        loaded_b = brand_b.load_youtube_credentials()
        assert loaded_b["token"] == "brand_b_token"

        # Verify credentials are isolated
        assert loaded_a["token"] != loaded_b["token"]

    def test_encryption_at_rest(self, temp_creds_dir, sample_youtube_creds):
        """Test credentials are encrypted on disk."""
        brand = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )
        brand.save_youtube_credentials(sample_youtube_creds)

        # Read raw file contents
        cred_file = temp_creds_dir / "examplebrand" / "youtube_oauth.json.enc"
        raw_contents = cred_file.read_bytes()

        # Verify not plaintext JSON
        try:
            json.loads(raw_contents)
            pytest.fail("Credentials should be encrypted, not plaintext JSON")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # Expected - encrypted data is not valid JSON

        # Verify sensitive data not in plaintext
        assert b"sample_access_token" not in raw_contents
        assert b"sample_refresh_token" not in raw_contents

    def test_decryption_with_correct_key(self, temp_creds_dir, sample_youtube_creds):
        """Test decryption works with correct key."""
        brand = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )
        brand.save_youtube_credentials(sample_youtube_creds)

        # Load credentials (should decrypt successfully)
        loaded_creds = brand.load_youtube_credentials()
        assert loaded_creds == sample_youtube_creds

    def test_decryption_fails_wrong_key(self, temp_creds_dir, sample_youtube_creds):
        """Test decryption fails with wrong key."""
        from cryptography.fernet import Fernet, InvalidToken

        # Save with one key
        brand = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )
        brand.save_youtube_credentials(sample_youtube_creds)

        # Try to load with different key
        key_file = temp_creds_dir / ".credentials_key"
        wrong_key = Fernet.generate_key()
        key_file.write_bytes(wrong_key)

        # Create new instance (will load wrong key)
        brand_wrong_key = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )

        # Should fail to decrypt
        with pytest.raises(InvalidToken):
            brand_wrong_key.load_youtube_credentials()

    def test_list_available_brands(self, temp_creds_dir, sample_youtube_creds):
        """Test listing all configured brands."""
        # Create multiple brands
        brand_a = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )
        brand_a.save_youtube_credentials(sample_youtube_creds)

        brand_b = BrandCredentials(
            brand_id="examplepodcast", credentials_dir=temp_creds_dir
        )
        brand_b.save_youtube_credentials(sample_youtube_creds)

        brand_c = BrandCredentials(brand_id="testbrand", credentials_dir=temp_creds_dir)
        brand_c.save_youtube_credentials(sample_youtube_creds)

        # List brands
        brands = BrandCredentials.list_brands(credentials_dir=temp_creds_dir)

        assert len(brands) == 3
        assert "examplebrand" in brands
        assert "examplepodcast" in brands
        assert "testbrand" in brands

    def test_fallback_to_legacy_files(self, temp_creds_dir, sample_youtube_creds):
        """Test backward compatibility with root credentials files."""
        # Create legacy credentials file
        legacy_file = temp_creds_dir / "youtube_credentials.json"
        with open(legacy_file, "w") as f:
            json.dump(sample_youtube_creds, f)

        # Try to load brand credentials (should fallback to legacy)
        brand = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )
        loaded_creds = brand.load_youtube_credentials()

        # Should load legacy credentials
        assert loaded_creds == sample_youtube_creds

    def test_migrate_legacy_to_multi_brand(self, temp_creds_dir, sample_youtube_creds):
        """Test migrating existing legacy credentials to multi-brand."""
        # Create legacy credentials
        legacy_file = temp_creds_dir / "youtube_credentials.json"
        with open(legacy_file, "w") as f:
            json.dump(sample_youtube_creds, f)

        # Migrate to multi-brand
        brand = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )
        brand.migrate_from_legacy()

        # Verify encrypted file created
        encrypted_file = temp_creds_dir / "examplebrand" / "youtube_oauth.json.enc"
        assert encrypted_file.exists()

        # Verify can load migrated credentials
        loaded_creds = brand.load_youtube_credentials()
        assert loaded_creds == sample_youtube_creds

    def test_client_secret_management(self, temp_creds_dir, sample_client_secret):
        """Test saving and loading OAuth client secret configuration."""
        brand = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )

        # Save client secret
        brand.save_client_secret(sample_client_secret)

        # Verify encrypted file created
        secret_file = temp_creds_dir / "examplebrand" / "client_secret.json.enc"
        assert secret_file.exists()

        # Load client secret
        loaded_secret = brand.load_client_secret()
        assert loaded_secret == sample_client_secret

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
    def test_file_permissions_600(self, temp_creds_dir, sample_youtube_creds):
        """Test encrypted files have restrictive permissions (0o600)."""
        brand = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )
        brand.save_youtube_credentials(sample_youtube_creds)

        # Check file permissions
        cred_file = temp_creds_dir / "examplebrand" / "youtube_oauth.json.enc"
        file_mode = cred_file.stat().st_mode

        # Get permission bits (last 9 bits)
        permissions = oct(file_mode)[-3:]

        # Should be 600 (owner read/write only)
        assert permissions == "600", f"Expected 600 permissions, got {permissions}"

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
    def test_key_file_permissions_600(self, temp_creds_dir):
        """Test encryption key file has restrictive permissions (0o600)."""
        BrandCredentials(brand_id="examplebrand", credentials_dir=temp_creds_dir)

        # Check key file permissions
        key_file = temp_creds_dir / ".credentials_key"
        file_mode = key_file.stat().st_mode

        # Get permission bits
        permissions = oct(file_mode)[-3:]

        # Should be 600 (owner read/write only)
        assert permissions == "600", f"Expected 600 permissions, got {permissions}"

    def test_invalid_brand_handling(self, temp_creds_dir):
        """Test clear error message when brand credentials not found."""
        brand = BrandCredentials(
            brand_id="nonexistent_brand", credentials_dir=temp_creds_dir
        )

        # Should raise clear error
        with pytest.raises(FileNotFoundError) as exc_info:
            brand.load_youtube_credentials()

        # Error message should mention the brand
        assert "nonexistent_brand" in str(exc_info.value)

    def test_brand_exists(self, temp_creds_dir, sample_youtube_creds):
        """Test checking if brand credentials exist."""
        brand = BrandCredentials(
            brand_id="examplebrand", credentials_dir=temp_creds_dir
        )

        # Should not exist initially
        assert not brand.exists()

        # Save credentials
        brand.save_youtube_credentials(sample_youtube_creds)

        # Should exist now
        assert brand.exists()
