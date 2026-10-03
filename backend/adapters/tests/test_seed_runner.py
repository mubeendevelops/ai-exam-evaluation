"""The seed runner's guards (no database needed)."""

from pathlib import Path

import pytest

from tarn_adapters.config import Settings
from tarn_adapters.seed import asset_reader, run_seed
from tarn_core.seed import SeedError


def test_the_seed_refuses_to_run_in_production() -> None:
    # model_construct skips the production checks: only the seed's own guard is under test
    settings = Settings.model_construct(env="production")
    with pytest.raises(SeedError, match="production"):
        run_seed(settings)


def test_assets_are_read_by_plain_name_only(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"x")
    read = asset_reader(tmp_path)
    assert read("a.png") == b"x"
    for name in ("missing.png", "../a.png", "sub/a.png"):
        with pytest.raises(SeedError):
            read(name)
