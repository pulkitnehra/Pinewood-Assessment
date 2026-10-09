"""Gold layer: star schema populated from Silver.

DDL lives in sql/gold_ddl.sql (grain of each fact documented there).
Everything here is a straight INSERT ... SELECT, fully rebuilt per run.

Day-count conventions:
- resident_days: inclusive of both admit and discharge day, clipped to the
  calendar month.
- occupied unit-days: inclusive of move-in day, exclusive of move-out day
  (the unit is turnable on move-out day).
"""

import config
import quality

DDL_PATH = config.PROJECT_ROOT / "sql" / "gold_ddl.sql"


def _log(con, run_id, table):
    n = con.execute(f'SELECT count(*) FROM gold."{table}"').fetchone()[0]
    quality.log_run(con, run_id, "gold", table, None, n, n, 0)
    print(f"[gold]   {table:<24} {n} rows")


def run(con, run_id):
    print("\n[gold] building star schema")
    con.execute(DDL_PATH.read_text(encoding="utf-8"))

    # ------------------------------------------------------------ dim_date
    con.execute(f"""
        INSERT INTO gold.dim_date
        SELECT d                                   AS date_key,
               year(d), quarter(d), month(d),
               strftime(d, '%B')                   AS month_name,
               date_trunc('month', d)              AS month_start,
               day(d),
               strftime(d, '%A')                   AS day_name,
               CAST(weekofyear(d) AS INTEGER)      AS iso_week,
               dayofweek(d) IN (0, 6)              AS is_weekend
        FROM (SELECT CAST(unnest(generate_series(DATE '{config.DIM_DATE_START}',
                                                 DATE '{config.DIM_DATE_END}',
                                                 INTERVAL 1 DAY)) AS DATE) AS d)
    """)
    _log(con, run_id, "dim_date")

    # ------------------------------------------------------- dim_community
    # No community master exists in any source: states/regions are a
    # documented assumption (config.COMMUNITY_STATE) pending client input.
    con.executemany(
        "INSERT INTO gold.dim_community VALUES (?, ?, ?, ?)",
        [[cid, f"Pinewood {cid}", state, config.STATE_REGION[state]]
         for cid, state in config.COMMUNITY_STATE.items()],
    )
    _log(con, run_id, "dim_community")

    # -------------------------------------------------------- dim_resident
    con.execute("""
        INSERT INTO gold.dim_resident
        SELECT resident_id, first_name, last_name,
               first_name || ' ' || last_name AS full_name,
               dob, gender
        FROM silver.residents
        QUALIFY row_number() OVER (PARTITION BY resident_id ORDER BY period DESC) = 1
    """)
    _log(con, run_id, "dim_resident")

    # ------------------------------------------------------------ dim_unit
    con.execute("""
        INSERT INTO gold.dim_unit
        SELECT unit_id, community_id, unit_type, monthly_rent AS list_rent
        FROM silver.units
        QUALIFY row_number() OVER (PARTITION BY unit_id ORDER BY period DESC) = 1
    """)
    _log(con, run_id, "dim_unit")

    # ------------------------------------------------------ dim_care_level
    con.executemany("INSERT INTO gold.dim_care_level VALUES (?, ?, ?)",
                    [list(row) for row in config.CARE_LEVELS])
    _log(con, run_id, "dim_care_level")

    # ------------------------------------------------- fact_resident_month
    con.execute("""
        INSERT INTO gold.fact_resident_month
        SELECT resident_id, community_id, care_level_code,
               period AS month_start,
               greatest(0, date_diff('day',
                    greatest(admit_date, period),
                    least(coalesce(discharge_date, last_day(period)), last_day(period))
               ) + 1) AS resident_days,
               acuity_score
        FROM silver.residents
        WHERE admit_date IS NOT NULL
    """)
    _log(con, run_id, "fact_resident_month")

    # ------------------------------------------------------- fact_incident
    con.execute("""
        INSERT INTO gold.fact_incident
        SELECT i.incident_id, i.incident_date, i.resident_id, i.community_id,
               r.care_level_code,
               i.incident_type, i.severity, i.reported_by
        FROM silver.incidents i
        LEFT JOIN silver.residents r
          ON r.resident_id = i.resident_id
         AND r.period = date_trunc('month', i.incident_date)
    """)
    _log(con, run_id, "fact_incident")

    # ---------------------------------------------------------- fact_lease
    con.execute("""
        INSERT INTO gold.fact_lease
        SELECT lease_id, resident_id, unit_id, community_id,
               move_in_date, move_out_date, move_out_reason, monthly_rate
        FROM silver.leases
    """)
    _log(con, run_id, "fact_lease")

    # ---------------------------------------------------------- fact_shift
    con.execute("""
        INSERT INTO gold.fact_shift
        SELECT shift_id, shift_date, community_id, employee_id, role,
               hours_worked, hourly_rate, labor_cost
        FROM silver.shifts
    """)
    _log(con, run_id, "fact_shift")

    # --------------------------------------------------------- fact_review
    con.execute("""
        INSERT INTO gold.fact_review
        SELECT review_id, review_date, community_id, rating,
               responded_at IS NOT NULL AS has_response,
               date_diff('day', review_date, responded_at) AS response_lag_days
        FROM silver.reviews
    """)
    _log(con, run_id, "fact_review")

    # ----------------------------------------------------------- fact_lead
    con.execute("""
        INSERT INTO gold.fact_lead
        SELECT lead_id, created_date, community_id, lead_source,
               tour_date, deposit_date, move_in_date, status, lost_reason
        FROM silver.leads
    """)
    _log(con, run_id, "fact_lead")

    # ----------------------------------------------- fact_care_level_change
    con.execute("""
        INSERT INTO gold.fact_care_level_change
        SELECT resident_id, change_date, previous_level_code, new_level_code, reason
        FROM silver.care_history
    """)
    _log(con, run_id, "fact_care_level_change")

    # ------------------------------------------------- fact_occupancy_month
    # Occupancy = census resident-days / available unit-days.
    # The lease export only contains leases created or ended inside the
    # window, so it cannot reconstruct the full census; the resident
    # snapshots can, which is why fact_resident_month is the numerator.
    con.execute("""
        INSERT INTO gold.fact_occupancy_month
        WITH avail AS (
            SELECT community_id, period AS month_start,
                   count(*) AS total_units,
                   count(*) * day(last_day(period)) AS available_unit_days
            FROM silver.units
            GROUP BY community_id, period
        ),
        census AS (
            SELECT community_id, month_start, sum(resident_days) AS resident_days
            FROM gold.fact_resident_month
            GROUP BY community_id, month_start
        )
        SELECT a.community_id, a.month_start, a.total_units, a.available_unit_days,
               coalesce(c.resident_days, 0) AS resident_days,
               round(coalesce(c.resident_days, 0) * 1.0 / a.available_unit_days, 4)
                   AS occupancy_pct
        FROM avail a
        LEFT JOIN census c
          ON c.community_id = a.community_id AND c.month_start = a.month_start
        ORDER BY a.community_id, a.month_start
    """)
    _log(con, run_id, "fact_occupancy_month")
