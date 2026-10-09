"""Export the Gold layer to Parquet files for Power BI.

Power BI Desktop has no native DuckDB connector, so instead of forcing an
ODBC driver install the semantic model imports these Parquet files. The PBIP
model's GoldFolder parameter points at the output directory.

DECIMAL columns are cast to DOUBLE so Power BI sees plain decimal numbers.
"""

import config

EXPORT_DIR = config.PROJECT_ROOT / "warehouse" / "export"


def run(con, run_id):
    print("\n[export] writing gold tables to parquet for Power BI")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    tables = [r[0] for r in con.execute(
        """SELECT table_name FROM information_schema.tables
           WHERE table_schema = 'gold' ORDER BY table_name"""
    ).fetchall()]

    for table in tables:
        cols = con.execute(
            """SELECT column_name, data_type FROM information_schema.columns
               WHERE table_schema = 'gold' AND table_name = ?
               ORDER BY ordinal_position""",
            [table],
        ).fetchall()
        select_list = ", ".join(
            f'CAST("{c}" AS DOUBLE) AS "{c}"' if t.startswith("DECIMAL") else f'"{c}"'
            for c, t in cols
        )
        out = (EXPORT_DIR / f"{table}.parquet").as_posix()
        con.execute(
            f"COPY (SELECT {select_list} FROM gold.\"{table}\") TO '{out}' (FORMAT PARQUET)"
        )
    print(f"[export] {len(tables)} tables -> {EXPORT_DIR}")
