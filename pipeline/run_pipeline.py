"""Pinewood medallion pipeline: CSVs -> Bronze -> Silver -> Gold (DuckDB).

Single command:
    python pipeline/run_pipeline.py
Optional:
    python pipeline/run_pipeline.py --data-dir <path-to-csvs> --db <path-to-duckdb>

Re-running never duplicates data: Bronze replaces rows per source file,
Silver and Gold are rebuilt from Bronze every run.
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb

import bronze
import config
import export
import gold
import quality
import silver


def main():
    parser = argparse.ArgumentParser(description="Pinewood Bronze/Silver/Gold pipeline")
    parser.add_argument("--data-dir", type=Path, default=config.DEFAULT_DATA_DIR,
                        help="Directory containing the source CSV files")
    parser.add_argument("--db", type=Path, default=config.DEFAULT_DB_PATH,
                        help="Path of the DuckDB database file to create/update")
    args = parser.parse_args()

    if not args.data_dir.is_dir():
        sys.exit(f"ERROR: data directory not found: {args.data_dir}")

    args.db.parent.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    started = time.time()

    print(f"Pinewood pipeline | run_id={run_id}")
    print(f"  data dir : {args.data_dir}")
    print(f"  warehouse: {args.db}")

    con = duckdb.connect(str(args.db))
    try:
        quality.init_meta(con)
        quality.clear_run(con, run_id)

        bronze.run(con, run_id, args.data_dir, config.SOURCE_TABLES)
        silver.run(con, run_id)
        gold.run(con, run_id)
        export.run(con, run_id)

        quality.print_summary(con, run_id)
        print(f"\nDone in {time.time() - started:.1f}s. Warehouse: {args.db}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
