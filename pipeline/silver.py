"""Silver layer: cleaned, typed, deduplicated tables built from Bronze.

Conventions:
- Silver is fully rebuilt from Bronze on every run (CREATE OR REPLACE), which
  makes re-runs trivially idempotent at this data volume.
- Every row that is dropped, and every value that is altered beyond plain
  typing, is logged to meta.quarantine with a reason.
- Dates: all sources are ISO except pcc_residents 2025-03, which is
  MM/DD/YYYY. meta.parse_date() handles both.
- Care levels: nine spellings in the raw data are normalized through
  meta.care_level_map to the canonical codes IL / AL / MC.
"""

import config
import quality


def _bronze_count(con, table):
    return con.execute(f'SELECT count(*) FROM bronze."{table}"').fetchone()[0]


def _silver_count(con, table):
    return con.execute(f'SELECT count(*) FROM silver."{table}"').fetchone()[0]


def _quarantine_count(con, run_id, source_table):
    return con.execute(
        "SELECT count(*) FROM meta.quarantine WHERE run_id = ? AND source_table = ?",
        [run_id, source_table],
    ).fetchone()[0]


def _has_column(con, schema, table, column):
    return con.execute(
        """SELECT count(*) FROM information_schema.columns
           WHERE table_schema = ? AND table_name = ? AND column_name = ?""",
        [schema, table, column],
    ).fetchone()[0] > 0


def _setup_helpers(con):
    # Date parser covering ISO (YYYY-MM-DD) and US (MM/DD/YYYY) formats.
    con.execute(
        """CREATE OR REPLACE MACRO meta.parse_date(x) AS
           CASE WHEN x IS NULL OR trim(x) = '' THEN NULL
                ELSE coalesce(try_cast(trim(x) AS DATE),
                              CAST(try_strptime(trim(x), '%m/%d/%Y') AS DATE))
           END"""
    )
    con.execute("CREATE OR REPLACE TABLE meta.care_level_map (raw VARCHAR, code VARCHAR)")
    con.executemany("INSERT INTO meta.care_level_map VALUES (?, ?)",
                    [[raw, code] for raw, code in config.CARE_LEVEL_MAP.items()])


def _log(con, run_id, table, rows_in):
    loaded = _silver_count(con, table)
    quarantined = _quarantine_count(con, run_id, f"silver.{table}")
    quality.log_run(con, run_id, "silver", table, None, rows_in, loaded, quarantined)
    print(f"[silver] {table:<18} {loaded} rows ({quarantined} quarantine entries)")


def _quarantine_sql(con, run_id, source_table, action, reason, select_row_sql):
    """Set-based quarantine insert. select_row_sql must yield
    (source_file, row_num, raw_row_json) columns."""
    con.execute(
        f"""INSERT INTO meta.quarantine
            (run_id, source_table, source_file, row_num, action, reason, raw_row)
            SELECT ?, ?, source_file, row_num, ?, ?, raw_row
            FROM ({select_row_sql})""",
        [run_id, f"silver.{source_table}", action, reason],
    )


# ---------------------------------------------------------------- residents
def _build_residents(con, run_id):
    rows_in = _bronze_count(con, "pcc_residents")
    # mobility_status only exists if the drifted 2025-04 file was ingested.
    mobility = ('trim(b.mobility_status) AS mobility_status'
                if _has_column(con, "bronze", "pcc_residents", "mobility_status")
                else "CAST(NULL AS VARCHAR) AS mobility_status")

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _res_ranked AS
        WITH typed AS (
            SELECT
                trim(b.resident_id)            AS resident_id,
                trim(b.community_id)           AS community_id,
                trim(b.first_name)             AS first_name,
                trim(b.last_name)              AS last_name,
                meta.parse_date(b.dob)         AS dob,
                upper(trim(b.gender))          AS gender,
                meta.parse_date(b.admit_date)      AS admit_date,
                meta.parse_date(b.discharge_date)  AS discharge_date,
                lower(trim(b.care_level))      AS care_level_raw,
                m.code                         AS care_level_code,
                try_cast(b.acuity_score AS INTEGER) AS acuity_raw,
                {mobility},
                b._period AS period, b._source_file AS source_file, b._row_num AS row_num
            FROM bronze.pcc_residents b
            LEFT JOIN meta.care_level_map m ON lower(trim(b.care_level)) = m.raw
        ),
        -- A resident duplicated within one month (e.g. 'Michael'/'Michaea'
        -- Johnson in 2025-02) keeps the row whose community matches the
        -- resident's most frequent community across all snapshots.
        modal AS (
            SELECT resident_id, community_id, count(*) AS n
            FROM typed GROUP BY resident_id, community_id
        )
        SELECT t.*,
               row_number() OVER (PARTITION BY t.resident_id, t.period
                                  ORDER BY mo.n DESC, t.row_num) AS rk
        FROM typed t
        JOIN modal mo ON t.resident_id = mo.resident_id AND t.community_id = mo.community_id
    """)

    _quarantine_sql(con, run_id, "residents", "rejected",
                    "duplicate_resident_in_month_nonmodal_community",
                    """SELECT source_file, row_num,
                              to_json(struct_pack(resident_id, community_id, first_name,
                                                  last_name, period)) AS raw_row
                       FROM _res_ranked WHERE rk > 1""")
    _quarantine_sql(con, run_id, "residents", "modified",
                    f"acuity_out_of_range_nulled",
                    f"""SELECT source_file, row_num,
                               to_json(struct_pack(resident_id, acuity_raw)) AS raw_row
                        FROM _res_ranked
                        WHERE rk = 1 AND acuity_raw IS NOT NULL
                          AND acuity_raw NOT BETWEEN {config.ACUITY_MIN} AND {config.ACUITY_MAX}""")
    _quarantine_sql(con, run_id, "residents", "modified",
                    "unmapped_care_level_nulled",
                    """SELECT source_file, row_num,
                              to_json(struct_pack(resident_id, care_level_raw)) AS raw_row
                       FROM _res_ranked
                       WHERE rk = 1 AND care_level_code IS NULL
                         AND care_level_raw IS NOT NULL AND care_level_raw <> ''""")

    con.execute(f"""
        CREATE OR REPLACE TABLE silver.residents AS
        SELECT resident_id, community_id, first_name, last_name, dob, gender,
               admit_date, discharge_date, care_level_code,
               CASE WHEN acuity_raw BETWEEN {config.ACUITY_MIN} AND {config.ACUITY_MAX}
                    THEN acuity_raw END AS acuity_score,
               mobility_status, period, source_file
        FROM _res_ranked WHERE rk = 1
    """)
    con.execute("DROP TABLE _res_ranked")
    _log(con, run_id, "residents", rows_in)


# ---------------------------------------------------------------- incidents
def _build_incidents(con, run_id):
    rows_in = _bronze_count(con, "pcc_incidents")
    con.execute("""
        CREATE OR REPLACE TABLE silver.incidents AS
        SELECT trim(incident_id)  AS incident_id,
               trim(resident_id)  AS resident_id,
               trim(community_id) AS community_id,
               meta.parse_date(incident_date) AS incident_date,
               trim(incident_type) AS incident_type,
               try_cast(severity AS INTEGER) AS severity,
               trim(reported_by) AS reported_by,
               _period AS period, _source_file AS source_file
        FROM bronze.pcc_incidents
        QUALIFY row_number() OVER (PARTITION BY trim(incident_id)
                                   ORDER BY _source_file DESC, _row_num) = 1
    """)
    _log(con, run_id, "incidents", rows_in)


# ------------------------------------------------------------- care_history
def _build_care_history(con, run_id):
    rows_in = _bronze_count(con, "pcc_care_history")
    con.execute("""
        CREATE OR REPLACE TABLE silver.care_history AS
        SELECT trim(b.resident_id) AS resident_id,
               meta.parse_date(b.change_date) AS change_date,
               mp.code AS previous_level_code,
               mn.code AS new_level_code,
               trim(b.reason) AS reason,
               b._period AS period, b._source_file AS source_file
        FROM bronze.pcc_care_history b
        LEFT JOIN meta.care_level_map mp ON lower(trim(b.previous_level)) = mp.raw
        LEFT JOIN meta.care_level_map mn ON lower(trim(b.new_level)) = mn.raw
    """)
    _log(con, run_id, "care_history", rows_in)


# -------------------------------------------------------------------- units
def _build_units(con, run_id):
    rows_in = _bronze_count(con, "yardi_units")
    valid = ", ".join(f"'{c}'" for c in config.VALID_COMMUNITIES)

    _quarantine_sql(con, run_id, "units", "rejected",
                    "unknown_community_id",
                    f"""SELECT _source_file AS source_file, _row_num AS row_num,
                               to_json(struct_pack(unit_id, community_id)) AS raw_row
                        FROM bronze.yardi_units
                        WHERE trim(community_id) NOT IN ({valid})""")

    con.execute(f"""
        CREATE OR REPLACE TABLE silver.units AS
        SELECT trim(unit_id)      AS unit_id,
               trim(community_id) AS community_id,
               upper(trim(unit_type)) AS unit_type,
               try_cast(monthly_rent AS DECIMAL(10,2)) AS monthly_rent,
               meta.parse_date(snapshot_date) AS snapshot_date,
               _period AS period, _source_file AS source_file
        FROM bronze.yardi_units
        WHERE trim(community_id) IN ({valid})
        QUALIFY row_number() OVER (PARTITION BY trim(unit_id), _period
                                   ORDER BY _row_num) = 1
    """)
    _log(con, run_id, "units", rows_in)


# ------------------------------------------------------------------- leases
def _build_leases(con, run_id):
    rows_in = _bronze_count(con, "yardi_leases")
    # A lease legitimately appears in its creation month's file and again in
    # its move-out month's file. Keep the latest file's copy.
    _quarantine_sql(con, run_id, "leases", "rejected",
                    "duplicate_lease_id_earlier_file_copy",
                    """SELECT source_file, row_num, raw_row FROM (
                           SELECT _source_file AS source_file, _row_num AS row_num,
                                  to_json(struct_pack(lease_id, resident_id, unit_id)) AS raw_row,
                                  row_number() OVER (PARTITION BY trim(lease_id)
                                                     ORDER BY _source_file DESC, _row_num) AS rk
                           FROM bronze.yardi_leases) WHERE rk > 1""")

    con.execute("""
        CREATE OR REPLACE TABLE silver.leases AS
        SELECT trim(lease_id)     AS lease_id,
               trim(resident_id)  AS resident_id,
               trim(unit_id)      AS unit_id,
               trim(community_id) AS community_id,
               meta.parse_date(move_in_date)  AS move_in_date,
               meta.parse_date(move_out_date) AS move_out_date,
               nullif(trim(move_out_reason), '') AS move_out_reason,
               try_cast(monthly_rate AS DECIMAL(10,2)) AS monthly_rate,
               _period AS period, _source_file AS source_file
        FROM bronze.yardi_leases
        QUALIFY row_number() OVER (PARTITION BY trim(lease_id)
                                   ORDER BY _source_file DESC, _row_num) = 1
    """)
    _log(con, run_id, "leases", rows_in)


# ------------------------------------------------------------------- shifts
def _build_shifts(con, run_id):
    rows_in = _bronze_count(con, "adp_shifts")
    # The ADP export serialized the whole role->rate map into hourly_rate
    # (every row, every month). Extract this row's rate by its role; fall back
    # to a plain numeric cast in case a future export is fixed.
    rate_expr = r"""
        coalesce(
            try_cast(hourly_rate AS DOUBLE),
            try_cast(regexp_extract(hourly_rate,
                     '''' || trim(role) || ''':\s*([0-9]+(?:\.[0-9]+)?)', 1) AS DOUBLE)
        )"""

    con.execute(f"""
        CREATE OR REPLACE TABLE silver.shifts AS
        SELECT trim(shift_id)     AS shift_id,
               trim(community_id) AS community_id,
               trim(employee_id)  AS employee_id,
               trim(role)         AS role,
               meta.parse_date(shift_date) AS shift_date,
               try_cast(hours_worked AS DOUBLE) AS hours_worked,
               {rate_expr} AS hourly_rate,
               round(try_cast(hours_worked AS DOUBLE) * {rate_expr}, 2) AS labor_cost,
               _period AS period, _source_file AS source_file
        FROM bronze.adp_shifts
        QUALIFY row_number() OVER (PARTITION BY trim(shift_id)
                                   ORDER BY _source_file DESC, _row_num) = 1
    """)

    _quarantine_sql(con, run_id, "shifts", "modified",
                    "hourly_rate_unresolvable_nulled",
                    """SELECT source_file, row_num,
                              to_json(struct_pack(shift_id, role)) AS raw_row
                       FROM (SELECT shift_id, role, source_file,
                                    NULL AS row_num, hourly_rate
                             FROM silver.shifts)
                       WHERE hourly_rate IS NULL""")
    _log(con, run_id, "shifts", rows_in)


# ------------------------------------------------------------------ reviews
def _build_reviews(con, run_id):
    rows_in = _bronze_count(con, "gbp_reviews")
    con.execute("""
        CREATE OR REPLACE TABLE silver.reviews AS
        SELECT trim(review_id)    AS review_id,
               trim(community_id) AS community_id,
               meta.parse_date(review_date) AS review_date,
               try_cast(rating AS INTEGER)  AS rating,
               nullif(trim(review_text), '')   AS review_text,
               nullif(trim(response_text), '') AS response_text,
               meta.parse_date(responded_at)   AS responded_at,
               _period AS period, _source_file AS source_file
        FROM bronze.gbp_reviews
        QUALIFY row_number() OVER (PARTITION BY trim(review_id)
                                   ORDER BY _source_file DESC, _row_num) = 1
    """)
    _log(con, run_id, "reviews", rows_in)


# -------------------------------------------------------------------- leads
def _build_leads(con, run_id):
    rows_in = _bronze_count(con, "hubspot_leads")
    _quarantine_sql(con, run_id, "leads", "rejected",
                    "duplicate_lead_id_earlier_copy",
                    """SELECT source_file, row_num, raw_row FROM (
                           SELECT _source_file AS source_file, _row_num AS row_num,
                                  to_json(struct_pack(lead_id, community_id)) AS raw_row,
                                  row_number() OVER (PARTITION BY trim(lead_id)
                                                     ORDER BY _source_file DESC, _row_num) AS rk
                           FROM bronze.hubspot_leads) WHERE rk > 1""")

    con.execute("""
        CREATE OR REPLACE TABLE silver.leads AS
        SELECT trim(lead_id)      AS lead_id,
               trim(community_id) AS community_id,
               trim(lead_source)  AS lead_source,
               meta.parse_date(created_date)  AS created_date,
               meta.parse_date(tour_date)     AS tour_date,
               meta.parse_date(deposit_date)  AS deposit_date,
               meta.parse_date(move_in_date)  AS move_in_date,
               trim(status) AS status,
               nullif(trim(lost_reason), '') AS lost_reason,
               _period AS period, _source_file AS source_file
        FROM bronze.hubspot_leads
        QUALIFY row_number() OVER (PARTITION BY trim(lead_id)
                                   ORDER BY _source_file DESC, _row_num) = 1
    """)
    _log(con, run_id, "leads", rows_in)


def run(con, run_id):
    print("\n[silver] building cleaned tables")
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    _setup_helpers(con)
    _build_residents(con, run_id)
    _build_incidents(con, run_id)
    _build_care_history(con, run_id)
    _build_units(con, run_id)
    _build_leases(con, run_id)
    _build_shifts(con, run_id)
    _build_reviews(con, run_id)
    _build_leads(con, run_id)
