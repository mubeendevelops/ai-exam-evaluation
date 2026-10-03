"""Development seed data (P8): two demo colleges, the sample papers and their keys."""

from tarn_core.seed.data.colleges import COLLEGES, COMMERCE, DEV_PASSWORD, ENGINEERING
from tarn_core.seed.model import SYNTHETIC_SOURCE
from tarn_core.seed.seeder import (
    SeededAccounts,
    SeededContent,
    SeedError,
    SeedReport,
    seed_accounts,
    seed_content,
)

__all__ = [
    "COLLEGES",
    "COMMERCE",
    "DEV_PASSWORD",
    "ENGINEERING",
    "SYNTHETIC_SOURCE",
    "SeedError",
    "SeedReport",
    "SeededAccounts",
    "SeededContent",
    "seed_accounts",
    "seed_content",
]
