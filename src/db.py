"""Database connection and data loading for the Hillstrom experiment.

Run as a script to build the database from scratch:

    python -m src.db

That downloads the CSV (if absent), applies ``sql/01_schema.sql``, loads all
64,000 rows, and refuses to exit successfully unless the row count is exactly
right.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.request import urlretrieve

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SQL_DIR = PROJECT_ROOT / "sql"
CSV_PATH = DATA_DIR / "hillstrom.csv"

DATA_URL = (
    "http://www.minethatdata.com/"
    "Kevin_Hillstrom_MineThatData_E-MailAnalytics_DataMiningChallenge_2008.03.20.csv"
)

EXPECTED_ROWS = 64_000

# The container is published on host port 5433, not 5432. See the comment in
# docker-compose.yml: an unrelated PostgreSQL server occupies 5432 on the
# development machine, and connecting there would load into the wrong
# database while appearing to work.
DEFAULT_PORT = "5433"


def get_engine() -> Engine:
    """Build a SQLAlchemy engine from .env, using the psycopg 3 driver."""
    load_dotenv(PROJECT_ROOT / ".env")

    user = os.getenv("POSTGRES_USER", "hillstrom")
    password = os.getenv("POSTGRES_PASSWORD", "hillstrom")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", DEFAULT_PORT)
    database = os.getenv("POSTGRES_DB", "hillstrom")

    # "postgresql+psycopg" selects psycopg 3. Plain "postgresql" would ask for
    # psycopg2, which this project does not install.
    url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"
    return create_engine(url, future=True)


def download_csv(force: bool = False) -> Path:
    """Fetch the raw CSV into data/ (gitignored). No-op if already present."""
    DATA_DIR.mkdir(exist_ok=True)
    if CSV_PATH.exists() and not force:
        return CSV_PATH
    print(f"Downloading {DATA_URL} ...")
    urlretrieve(DATA_URL, CSV_PATH)
    return CSV_PATH


def read_csv() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH)
    if len(df) != EXPECTED_ROWS:
        raise ValueError(f"CSV has {len(df):,} rows, expected {EXPECTED_ROWS:,}")
    return df


def apply_schema(engine: Engine) -> None:
    schema_sql = (SQL_DIR / "01_schema.sql").read_text()
    with engine.begin() as conn:
        conn.execute(text(schema_sql))
    print("Applied sql/01_schema.sql")


def load_dataframe(engine: Engine, df: pd.DataFrame) -> None:
    """Append the CSV into `customers`, letting customer_id autogenerate."""
    # method="multi" packs a chunk into one multi-row INSERT, and PostgreSQL
    # caps a single statement at 65,535 bound parameters. With 12 columns that
    # allows 5,461 rows per chunk, so stay comfortably under it.
    max_params = 65_535
    chunksize = min(5_000, max_params // (len(df.columns) + 1))
    df.to_sql(
        "customers",
        engine,
        if_exists="append",
        index=False,
        chunksize=chunksize,
        method="multi",
    )
    print(f"Loaded {len(df):,} rows into customers")


def verify(engine: Engine) -> None:
    """Assert the load is correct and that we hit the container, not another
    PostgreSQL server that happens to be running on this machine."""
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar_one()
        count = conn.execute(text("SELECT COUNT(*) FROM customers")).scalar_one()
        arms = conn.execute(
            text("SELECT COUNT(DISTINCT segment) FROM customers")
        ).scalar_one()

    print(f"Server: {version.split(',')[0]}")
    if count != EXPECTED_ROWS:
        raise AssertionError(f"Expected {EXPECTED_ROWS:,} rows, found {count:,}")
    if arms != 3:
        raise AssertionError(f"Expected 3 arms, found {arms}")
    print(f"Verified: {count:,} rows across {arms} arms")


def main() -> int:
    engine = get_engine()
    download_csv()
    df = read_csv()
    apply_schema(engine)
    load_dataframe(engine, df)
    verify(engine)
    print("\nDatabase ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
