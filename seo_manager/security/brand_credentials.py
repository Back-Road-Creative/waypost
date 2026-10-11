"""
Multi-brand credentials management with encryption.

Features:
- Directory-based brand isolation (credentials/brand_id/)
- Lightweight encryption using Fernet
- File permissions enforcement (600)
- Backward compatibility with legacy root files
- Easy migration from single-brand to multi-brand

Example:
    >>> from seo_manager.security import BrandCredentials
    >>> brand = BrandCredentials(brand_id='mybrand')
    >>> brand.save_youtube_credentials(credentials)
    >>> creds = brand.load_youtube_credentials()
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.fernet import Fernet


class BrandCredentials:
    """
    Simple multi-brand credential manager.

    Provides:
    - Encrypted storage of OAuth credentials
    - Brand isolation (each brand has separate directory)
    - Backward compatibility with legacy files
    - Restrictive file permissions (600)
    """

    def __init__(self, brand_id: str, credentials_dir: Optional[Path] = None):
        """
        Initialize brand credentials manager.

        Args:
            brand_id: Unique identifier for the brand (e.g., 'mybrand')
            credentials_dir: Optional custom credentials directory path
        """
        self.brand_id = brand_id
        self.credentials_dir = credentials_dir or Path("./credentials")
        self.brand_dir = self.credentials_dir / brand_id

        # Load or create encryption key
        self.key = self._load_or_create_key()
        self.fernet = Fernet(self.key)

    def save_youtube_credentials(self, credentials: Dict) -> None:
        """
        Save YouTube OAuth credentials (encrypted).

        Args:
            credentials: YouTube OAuth credentials dictionary

        Creates:
            credentials/{brand_id}/youtube_oauth.json.enc (encrypted, 600 permissions)
        """
        # Create brand directory
        self.brand_dir.mkdir(parents=True, exist_ok=True)

        # Encrypt credentials
        encrypted = self._encrypt(credentials)

        # Save to file
        cred_file = self.brand_dir / "youtube_oauth.json.enc"
        cred_file.write_bytes(encrypted)

        # Set restrictive permissions
        cred_file.chmod(0o600)

    def load_youtube_credentials(self) -> Dict:
        """
        Load YouTube OAuth credentials (decrypt).

        Returns:
            Decrypted YouTube OAuth credentials

        Raises:
            FileNotFoundError: If credentials not found for brand

        Fallback:
            If encrypted file not found, tries legacy location
        """
        cred_file = self.brand_dir / "youtube_oauth.json.enc"

        if not cred_file.exists():
            # Try fallback to legacy location
            return self._load_legacy_credentials()

        # Decrypt and return
        encrypted = cred_file.read_bytes()
        return self._decrypt(encrypted)

    def save_client_secret(self, client_secret: Dict) -> None:
        """
        Save OAuth client secret configuration (encrypted).

        Args:
            client_secret: OAuth client configuration dictionary

        Creates:
            credentials/{brand_id}/client_secret.json.enc (encrypted, 600 permissions)
        """
        # Create brand directory
        self.brand_dir.mkdir(parents=True, exist_ok=True)

        # Encrypt
        encrypted = self._encrypt(client_secret)

        # Save to file
        secret_file = self.brand_dir / "client_secret.json.enc"
        secret_file.write_bytes(encrypted)

        # Set restrictive permissions
        secret_file.chmod(0o600)

    def load_client_secret(self) -> Dict:
        """
        Load OAuth client secret configuration (decrypt).

        Returns:
            Decrypted OAuth client configuration

        Raises:
            FileNotFoundError: If client secret not found for brand
        """
        secret_file = self.brand_dir / "client_secret.json.enc"

        if not secret_file.exists():
            # Try legacy location
            legacy_file = self.credentials_dir / "client_secret.json"
            if legacy_file.exists():
                with open(legacy_file) as f:
                    return json.load(f)
            raise FileNotFoundError(
                f"No client secret found for brand: {self.brand_id}"
            )

        # Decrypt and return
        encrypted = secret_file.read_bytes()
        return self._decrypt(encrypted)

    def exists(self) -> bool:
        """
        Check if brand credentials exist.

        Returns:
            True if encrypted credentials file exists
        """
        cred_file = self.brand_dir / "youtube_oauth.json.enc"
        return cred_file.exists()

    def migrate_from_legacy(self) -> None:
        """
        Migrate legacy credentials to encrypted multi-brand format.

        Reads from:
            credentials/youtube_credentials.json (legacy)

        Writes to:
            credentials/{brand_id}/youtube_oauth.json.enc (encrypted)

        Raises:
            FileNotFoundError: If legacy credentials not found
        """
        # Try multiple legacy locations
        legacy_locations = [
            self.credentials_dir / "youtube_credentials.json",
            Path("youtube_credentials.json"),  # Root directory
        ]

        legacy_creds = None
        for legacy_file in legacy_locations:
            if legacy_file.exists():
                with open(legacy_file) as f:
                    legacy_creds = json.load(f)
                break

        if legacy_creds is None:
            raise FileNotFoundError(
                "No legacy credentials found to migrate. "
                f"Searched: {', '.join(str(f) for f in legacy_locations)}"
            )

        # Save as encrypted multi-brand credentials
        self.save_youtube_credentials(legacy_creds)

    @staticmethod
    def list_brands(credentials_dir: Optional[Path] = None) -> List[str]:
        """
        List all configured brands.

        Args:
            credentials_dir: Optional custom credentials directory

        Returns:
            List of brand IDs with credentials
        """
        creds_dir = credentials_dir or Path("./credentials")

        if not creds_dir.exists():
            return []

        brands = []
        for item in creds_dir.iterdir():
            if item.is_dir() and not item.name.startswith("."):  # noqa: SIM102
                # Check if it has credential files
                if (item / "youtube_oauth.json.enc").exists():
                    brands.append(item.name)

        return sorted(brands)

    def _encrypt(self, data: Dict) -> bytes:
        """
        Encrypt data with Fernet.

        Args:
            data: Dictionary to encrypt

        Returns:
            Encrypted bytes
        """
        json_data = json.dumps(data, indent=2)
        return self.fernet.encrypt(json_data.encode("utf-8"))

    def _decrypt(self, data: bytes) -> Dict:
        """
        Decrypt data with Fernet.

        Args:
            data: Encrypted bytes

        Returns:
            Decrypted dictionary

        Raises:
            InvalidToken: If decryption fails (wrong key)
        """
        decrypted = self.fernet.decrypt(data)
        return json.loads(decrypted.decode("utf-8"))

    def _load_or_create_key(self) -> bytes:
        """
        Load existing encryption key or create new one.

        Returns:
            Fernet encryption key (32 bytes, base64-encoded)

        Creates:
            credentials/.credentials_key (600 permissions)
        """
        # Ensure credentials directory exists
        self.credentials_dir.mkdir(parents=True, exist_ok=True)

        key_file = self.credentials_dir / ".credentials_key"

        if key_file.exists():
            # Load existing key
            return key_file.read_bytes()
        else:
            # Generate new key
            key = Fernet.generate_key()
            key_file.write_bytes(key)

            # Set restrictive permissions
            key_file.chmod(0o600)

            return key

    def _load_legacy_credentials(self) -> Dict:
        """
        Load credentials from legacy location (backward compatibility).

        Tries:
            1. credentials/youtube_credentials.json
            2. youtube_credentials.json (only if credentials_dir is default './credentials')

        Returns:
            Legacy credentials dictionary

        Raises:
            FileNotFoundError: If no legacy credentials found
        """
        # Try credentials directory first
        legacy_file = self.credentials_dir / "youtube_credentials.json"
        if legacy_file.exists():
            with open(legacy_file) as f:
                return json.load(f)

        # Try root directory only if using default credentials directory
        # This prevents test isolation issues
        if self.credentials_dir == Path("./credentials"):
            root_legacy = Path("youtube_credentials.json")
            if root_legacy.exists():
                with open(root_legacy) as f:
                    return json.load(f)

        # No credentials found
        raise FileNotFoundError(
            f"No credentials found for brand: {self.brand_id}. "
            f"Please run OAuth flow or migrate legacy credentials."
        )
