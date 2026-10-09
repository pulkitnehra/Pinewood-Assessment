"""Generate the TMDL table files of the Pinewood semantic model.

Reads the gold schema from warehouse/pinewood.duckdb and writes one
definition/tables/<table>.tmdl per gold table: typed columns, an import
partition reading the table's Parquet export (via the GoldFolder parameter),
and the DAX measures defined below.

Run after a schema change in the Gold layer:
    python powerbi/generate_tables.py
The rest of the semantic model (model.tmdl, relationships.tmdl, roles,
expressions.tmdl, tables/_Measures.tmdl) is hand-maintained and not touched
by this script.

NOTE: all DAX measures live in the dedicated _Measures table
(tables/_Measures.tmdl), NOT on the fact tables, so this generator emits
columns and partitions only. Also note Power BI Desktop enriches the table
files on save (variations, annotations); regenerating overwrites that, so
only rerun this after a Gold schema change and re-save from Desktop after.
"""

import uuid
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent
DB = ROOT.parent / "warehouse" / "pinewood.duckdb"
TABLES_DIR = ROOT / "Pinewood.SemanticModel" / "definition" / "tables"

# DuckDB type -> (TMDL dataType). DECIMAL is exported to parquet as DOUBLE.
TYPE_MAP = {
    "VARCHAR": "string",
    "DATE": "dateTime",
    "TIMESTAMP": "dateTime",
    "INTEGER": "int64",
    "BIGINT": "int64",
    "DOUBLE": "double",
    "BOOLEAN": "boolean",
}

# Kept for reference only: the authoritative copies of these measures now
# live in tables/_Measures.tmdl and are no longer emitted into table files.
_MEASURES_REFERENCE = {
    "fact_occupancy_month": [
        ("Occupancy %",
         "DIVIDE(SUM(fact_occupancy_month[resident_days]), "
         "SUM(fact_occupancy_month[available_unit_days]))",
         "0.0%"),
        ("Current Occupancy %",
         """VAR LatestMonth =
    CALCULATE(MAX(fact_occupancy_month[month_start]), REMOVEFILTERS())
RETURN
    CALCULATE(
        [Occupancy %],
        REMOVEFILTERS(dim_date),
        fact_occupancy_month[month_start] = LatestMonth
    )""",
         "0.0%"),
        ("Occupancy % Prior Month",
         "CALCULATE([Occupancy %], DATEADD(dim_date[date_key], -1, MONTH))",
         "0.0%"),
        ("Occupancy % MoM Delta",
         "[Occupancy %] - [Occupancy % Prior Month]",
         "+0.0%;-0.0%;0.0%"),
    ],
    "fact_resident_month": [
        ("Census (Active Residents)",
         "DISTINCTCOUNT(fact_resident_month[resident_id])", "#,0"),
        ("Current Census",
         """VAR LatestMonth =
    CALCULATE(MAX(fact_resident_month[month_start]), REMOVEFILTERS())
RETURN
    CALCULATE(
        DISTINCTCOUNT(fact_resident_month[resident_id]),
        REMOVEFILTERS(dim_date),
        fact_resident_month[month_start] = LatestMonth
    )""",
         "#,0"),
        ("Resident Days", "SUM(fact_resident_month[resident_days])", "#,0"),
    ],
    "fact_incident": [
        ("Incidents", "COUNTROWS(fact_incident)", "#,0"),
        ("Incident Rate per 100 Resident-Days",
         "DIVIDE(COUNTROWS(fact_incident), "
         "SUM(fact_resident_month[resident_days])) * 100",
         "0.00"),
    ],
    "fact_lease": [
        ("Move-Outs",
         "CALCULATE(COUNTROWS(fact_lease), fact_lease[move_out_date] <> BLANK())",
         "#,0"),
        ("Move-Out Rate % (Trailing 90D)",
         """VAR WindowEnd =
    CALCULATE(EOMONTH(MAX(fact_resident_month[month_start]), 0), REMOVEFILTERS())
VAR WindowStart = WindowEnd - 89
VAR MoveOuts90 =
    CALCULATE(
        COUNTROWS(fact_lease),
        REMOVEFILTERS(dim_date),
        fact_lease[move_out_date] >= WindowStart,
        fact_lease[move_out_date] <= WindowEnd
    )
VAR ResidentsInWindow =
    CALCULATE(
        DISTINCTCOUNT(fact_resident_month[resident_id]),
        REMOVEFILTERS(dim_date),
        fact_resident_month[month_start] >= DATE(YEAR(WindowStart), MONTH(WindowStart), 1)
    )
RETURN
    DIVIDE(MoveOuts90, ResidentsInWindow)""",
         "0.0%"),
    ],
    "fact_review": [
        ("Reviews", "COUNTROWS(fact_review)", "#,0"),
        ("Avg Rating", "AVERAGE(fact_review[rating])", "0.00"),
    ],
    "fact_shift": [
        ("Labor Hours", "SUM(fact_shift[hours_worked])", "#,0"),
        ("Labor Cost", "SUM(fact_shift[labor_cost])", "\\$#,0"),
    ],
    "fact_lead": [
        ("Leads", "COUNTROWS(fact_lead)", "#,0"),
        ("Lead Conversion %",
         "DIVIDE(CALCULATE(COUNTROWS(fact_lead), fact_lead[status] = \"Won\"), "
         "COUNTROWS(fact_lead))",
         "0.0%"),
    ],
}


def _tag():
    return str(uuid.uuid4())


def _measure_block(name, dax, fmt):
    lines = [f"\tmeasure '{name}' ="]
    for dl in dax.splitlines():
        lines.append(f"\t\t\t{dl}" if dl.strip() else "")
    lines.append(f"\t\tformatString: {fmt}")
    lines.append(f"\t\tlineageTag: {_tag()}")
    lines.append("")
    return lines


def generate_table(con, table):
    cols = con.execute(
        """SELECT column_name, data_type FROM information_schema.columns
           WHERE table_schema = 'gold' AND table_name = ?
           ORDER BY ordinal_position""",
        [table],
    ).fetchall()

    lines = [f"table {table}", f"\tlineageTag: {_tag()}"]
    if table == "dim_date":
        lines.append("\tdataCategory: Time")
    lines.append("")

    for col, dtype in cols:
        base = dtype.split("(")[0]
        tmdl_type = "double" if base == "DECIMAL" else TYPE_MAP[base]
        lines.append(f"\tcolumn {col}")
        lines.append(f"\t\tdataType: {tmdl_type}")
        if table == "dim_date" and col == "date_key":
            lines.append("\t\tisKey")
        if tmdl_type == "dateTime":
            lines.append("\t\tformatString: yyyy-mm-dd")
        lines.append(f"\t\tlineageTag: {_tag()}")
        lines.append("\t\tsummarizeBy: none")
        lines.append(f"\t\tsourceColumn: {col}")
        lines.append("")

    lines.append(f"\tpartition {table} = m")
    lines.append("\t\tmode: import")
    lines.append("\t\tsource =")
    lines.append("\t\t\t\tlet")
    lines.append(f'\t\t\t\t    Source = Parquet.Document(File.Contents(GoldFolder & "{table}.parquet"))')
    lines.append("\t\t\t\tin")
    lines.append("\t\t\t\t    Source")
    lines.append("")
    return "\n".join(lines) + "\n"


def main():
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB), read_only=True)
    tables = [r[0] for r in con.execute(
        """SELECT table_name FROM information_schema.tables
           WHERE table_schema = 'gold' ORDER BY table_name"""
    ).fetchall()]
    for t in tables:
        (TABLES_DIR / f"{t}.tmdl").write_text(generate_table(con, t), encoding="utf-8")
        print("wrote", TABLES_DIR / f"{t}.tmdl")
    con.close()


if __name__ == "__main__":
    main()
