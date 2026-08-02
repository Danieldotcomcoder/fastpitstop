"""Writes clean historical tables as Hive-style partitioned parquet.

Layout: `<out_dir>/season=<YYYY>/round=<NN>/session=<slug>/<table>.parquet`
— the `key=value` directory naming is what makes this directly
DuckDB/pandas partition-discoverable without a database (`SELECT * FROM
read_parquet('data/historical/**/*.parquet', hive_partitioning=true)`).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from f1_backfill.schedule import SessionKey, slugify


def session_dir(out_dir: Path, key: SessionKey) -> Path:
    return (
        out_dir
        / f"season={key.season}"
        / f"round={key.round:02d}"
        / f"session={slugify(key.session_type)}"
    )


def write_tables(out_dir: Path, key: SessionKey, tables: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Write every non-empty table for `key`, returning `{table: row_count}`.

    A table with zero columns (nothing was returned for it at all, e.g. no
    OpenF1 match) is skipped entirely rather than writing a schema-less
    parquet file; a table with zero rows but real columns is still written
    (an empty-but-typed result is meaningful and worth recording).
    """
    directory = session_dir(out_dir, key)
    directory.mkdir(parents=True, exist_ok=True)

    row_counts: dict[str, int] = {}
    for name, df in tables.items():
        if df.columns.empty:
            continue
        df.to_parquet(directory / f"{name}.parquet", engine="pyarrow", index=False)
        row_counts[name] = len(df)
    return row_counts
