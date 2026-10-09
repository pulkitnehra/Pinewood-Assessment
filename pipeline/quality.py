"""Meta-layer helpers: run log, quarantine, and the end-of-run summary."""

import json

DDL = """
CREATE SCHEMA IF NOT EXISTS meta;

CREATE TABLE IF NOT EXISTS meta.run_log (
    run_id            VARCHAR,
    layer             VARCHAR,
    table_name        VARCHAR,
    files_processed   INTEGER,
    rows_in           INTEGER,
    rows_loaded       INTEGER,
    rows_quarantined  INTEGER,
    logged_at         TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS meta.quarantine (
    run_id        VARCHAR,
    source_table  VARCHAR,
    source_file   VARCHAR,
    row_num       INTEGER,
    action        VARCHAR,   -- 'rejected' (row dropped) or 'modified' (value fixed/nulled)
    reason        VARCHAR,
    raw_row       VARCHAR,   -- JSON of the offending row / value
    logged_at     TIMESTAMP DEFAULT current_timestamp
);
"""


def init_meta(con):
    con.execute(DDL)


def clear_run(con, run_id):
    """Make re-runs idempotent at the meta level too."""
    con.execute("DELETE FROM meta.run_log WHERE run_id = ?", [run_id])
    con.execute("DELETE FROM meta.quarantine WHERE run_id = ?", [run_id])


def log_run(con, run_id, layer, table_name, files_processed, rows_in, rows_loaded, rows_quarantined):
    con.execute(
        """INSERT INTO meta.run_log
           (run_id, layer, table_name, files_processed, rows_in, rows_loaded, rows_quarantined)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [run_id, layer, table_name, files_processed, rows_in, rows_loaded, rows_quarantined],
    )


def quarantine_row(con, run_id, source_table, source_file, row_num, action, reason, raw):
    con.execute(
        """INSERT INTO meta.quarantine
           (run_id, source_table, source_file, row_num, action, reason, raw_row)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [run_id, source_table, source_file, row_num, action, reason,
         json.dumps(raw, default=str)],
    )


def print_summary(con, run_id):
    print("\n" + "=" * 78)
    print(f"RUN SUMMARY  (run_id = {run_id})")
    print("=" * 78)
    rows = con.execute(
        """SELECT layer, table_name, files_processed, rows_in, rows_loaded, rows_quarantined
           FROM meta.run_log WHERE run_id = ?
           ORDER BY CASE layer WHEN 'bronze' THEN 1 WHEN 'silver' THEN 2 ELSE 3 END, table_name""",
        [run_id],
    ).fetchall()
    print(f"{'layer':<8} {'table':<28} {'files':>5} {'rows_in':>9} {'loaded':>9} {'quarantined':>11}")
    print("-" * 78)
    for layer, tbl, files, rin, rload, rq in rows:
        print(f"{layer:<8} {tbl:<28} {files if files is not None else '-':>5} "
              f"{rin:>9} {rload:>9} {rq:>11}")

    q = con.execute(
        """SELECT source_table, action, reason, count(*)
           FROM meta.quarantine WHERE run_id = ?
           GROUP BY ALL ORDER BY source_table, reason""",
        [run_id],
    ).fetchall()
    if q:
        print("\nDATA QUALITY DETAIL (meta.quarantine)")
        print("-" * 78)
        for tbl, action, reason, n in q:
            print(f"  {tbl:<22} {action:<9} {reason:<35} {n:>5} row(s)")
    print("=" * 78)
