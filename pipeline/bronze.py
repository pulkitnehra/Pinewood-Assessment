"""Bronze layer: raw CSV ingestion.

Rules:
- Every value lands as VARCHAR exactly as it appeared in the file (DuckDB
  read_csv with all_varchar). No cleaning.
- Each row carries ingestion metadata (_source_file, _period, _row_num,
  _run_id, _ingested_at).
- Schema drift is absorbed: a monthly file with an extra column (e.g.
  mobility_status in 2025-04 residents) triggers ALTER TABLE ADD COLUMN;
  files without that column simply load NULL there.
- Malformed rows are skipped by the reader (ignore_errors) and logged to
  meta.quarantine via DuckDB's reject tables.
- Idempotent per file: rows for a _source_file are deleted before reload.
"""

import csv
import re
from datetime import datetime, timezone

import quality

FILE_RE_TEMPLATE = r"^{table}_(\d{{4}})_(\d{{2}})\.csv$"


def _safe_identifier(name):
    """Normalize a CSV header into a safe SQL column name."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name.strip().lower())
    return cleaned or "unnamed"


def _read_header(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        try:
            return next(csv.reader(fh))
        except StopIteration:
            return None


def _existing_columns(con, table):
    rows = con.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'bronze' AND table_name = ?""",
        [table],
    ).fetchall()
    return [r[0] for r in rows]


def _ensure_table(con, table, data_columns):
    cols = ", ".join(f'"{c}" VARCHAR' for c in data_columns)
    con.execute(
        f"""CREATE TABLE IF NOT EXISTS bronze."{table}" (
                {cols},
                _source_file VARCHAR,
                _period      DATE,
                _row_num     BIGINT,
                _run_id      VARCHAR,
                _ingested_at TIMESTAMP
            )"""
    )
    # Schema drift: add any column this file has that the table does not.
    existing = set(_existing_columns(con, table))
    for c in data_columns:
        if c not in existing:
            con.execute(f'ALTER TABLE bronze."{table}" ADD COLUMN "{c}" VARCHAR')


def _load_file(con, run_id, table, path, period):
    """Bulk-load one CSV into bronze.<table> via DuckDB's CSV reader.
    Returns (rows_loaded, rows_rejected)."""
    header = _read_header(path)
    if not header:
        quality.quarantine_row(con, run_id, table, path.name, 0,
                               "rejected", "empty_file", {})
        return 0, 0

    columns = [_safe_identifier(h) for h in header]
    _ensure_table(con, table, columns)

    # Idempotency: re-running the pipeline replaces this file's rows.
    con.execute(f'DELETE FROM bronze."{table}" WHERE _source_file = ?', [path.name])

    col_list = ", ".join(f'"{c}"' for c in columns)
    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    con.execute(
        f"""INSERT INTO bronze."{table}"
                ({col_list}, _source_file, _period, _row_num, _run_id, _ingested_at)
            SELECT *, ?, ?::DATE, row_number() OVER () + 1, ?, ?::TIMESTAMP
            FROM read_csv(?, header = true, all_varchar = true,
                          ignore_errors = true, store_rejects = true)""",
        [path.name, period, run_id, ingested_at, str(path)],
    )
    rows_loaded = con.execute(
        f'SELECT count(*) FROM bronze."{table}" WHERE _source_file = ?', [path.name]
    ).fetchone()[0]

    # Rows the CSV reader could not parse (e.g. ragged rows) land in DuckDB's
    # reject table; copy them into our quarantine with their line numbers.
    rejects = con.execute(
        "SELECT line, error_message, csv_line FROM reject_errors"
    ).fetchall()
    for line, message, csv_line in rejects:
        quality.quarantine_row(con, run_id, table, path.name, line,
                               "rejected", f"csv_parse_error: {message}",
                               {"line": csv_line})
    con.execute("DELETE FROM reject_errors")
    con.execute("DELETE FROM reject_scans")

    return rows_loaded, len(rejects)


def run(con, run_id, data_dir, source_tables):
    print("\n[bronze] ingesting raw CSVs from", data_dir)
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")

    for table in source_tables:
        pattern = re.compile(FILE_RE_TEMPLATE.format(table=re.escape(table)))
        files = sorted(p for p in data_dir.iterdir() if pattern.match(p.name))
        if not files:
            print(f"[bronze] WARNING: no files found for {table}")
            quality.log_run(con, run_id, "bronze", table, 0, 0, 0, 0)
            continue

        total_loaded = total_rejected = 0
        for path in files:
            m = pattern.match(path.name)
            period = f"{m.group(1)}-{m.group(2)}-01"
            loaded, rejected = _load_file(con, run_id, table, path, period)
            total_loaded += loaded
            total_rejected += rejected

        quality.log_run(con, run_id, "bronze", table, len(files),
                        total_loaded + total_rejected, total_loaded, total_rejected)
        print(f"[bronze] {table:<18} {len(files)} files, "
              f"{total_loaded} rows loaded, {total_rejected} rejected")
