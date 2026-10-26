"""Brand identifiers cannot select credential paths outside their directory."""

import os

import pytest

from seo_manager.security.brand_credentials import BrandCredentials


@pytest.mark.parametrize(
    "brand_id",
    [
        "",
        ".",
        "..",
        "../other",
        "nested/brand",
        r"nested\brand",
        "/outside",
        r"C:\outside",
        "C:outside",
        "bad\x00name",
    ],
)
def test_invalid_brand_is_rejected_before_creating_key(brand_id, tmp_path):
    root = tmp_path / "credentials"
    with pytest.raises(ValueError, match="brand_id"):
        BrandCredentials(brand_id, root)
    assert not root.exists()


@pytest.mark.parametrize("target_kind", ["outside", "other_brand"])
def test_brand_symlink_is_rejected_before_loading_key(target_kind, tmp_path):
    root = tmp_path / "credentials"
    root.mkdir()
    target = tmp_path / "outside" if target_kind == "outside" else root / "other"
    target.mkdir()
    (root / "brand").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="brand_id"):
        BrandCredentials("brand", root)
    assert not (root / ".credentials_key").exists()
    assert list(target.iterdir()) == []


def test_normal_brand_round_trips_in_its_own_directory(tmp_path):
    root = tmp_path / "credentials"
    brand = BrandCredentials("Brand_1.v2-test", root)
    brand.save_youtube_credentials({"example": "synthetic"})
    assert brand.load_youtube_credentials() == {"example": "synthetic"}
    assert brand.brand_dir == root / "Brand_1.v2-test"
    if os.name == "posix":  # Windows has no POSIX permission bits
        assert (
            brand.brand_dir / "youtube_oauth.json.enc"
        ).stat().st_mode & 0o777 == 0o600
