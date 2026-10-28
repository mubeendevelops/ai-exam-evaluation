"""Development seed data (P8): two demo colleges, the sample papers and their keys."""

from tarn_core.seed.data.colleges import COLLEGES, COMMERCE, DEV_PASSWORD, ENGINEERING
from tarn_core.seed.model import SYNTHETIC_SOURCE
from tarn_core.seed.seeder import (
    MailCatcher,
    SeededAccounts,
    SeededContent,
    SeedError,
    SeedReport,
    seed_accounts,
    seed_content,
    seed_units,
)

__all__ = [
    "COLLEGES",
    "COMMERCE",
    "DEV_PASSWORD",
    "ENGINEERING",
    "SYNTHETIC_SOURCE",
    "MailCatcher",
    "SeedError",
    "SeedReport",
    "SeededAccounts",
    "SeededContent",
    "seed_accounts",
    "seed_content",
    "seed_units",
]
