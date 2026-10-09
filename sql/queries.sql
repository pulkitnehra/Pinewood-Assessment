-- ===========================================================================
-- Part 2 analysis queries. Run against warehouse/pinewood.duckdb
-- (DuckDB CLI, or any DuckDB client).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. Monthly occupancy rate by community.
--    Occupancy = census resident-days / available unit-days.
--    gold.fact_occupancy_month pre-computes exactly this grain
--    (community x month), so the query is a straight read of the mart.
-- ---------------------------------------------------------------------------
SELECT
    community_id,
    month_start,
    total_units,
    resident_days,
    available_unit_days,
    round(occupancy_pct * 100, 1) AS occupancy_rate_pct
FROM gold.fact_occupancy_month
ORDER BY community_id, month_start;

-- ---------------------------------------------------------------------------
-- 2. Top three move-out reasons by community for the six months,
--    as a percentage of that community's total move-outs.
-- ---------------------------------------------------------------------------
WITH move_outs AS (
    SELECT community_id, move_out_reason, count(*) AS n
    FROM gold.fact_lease
    WHERE move_out_date IS NOT NULL
      AND move_out_reason IS NOT NULL
    GROUP BY community_id, move_out_reason
),
ranked AS (
    SELECT *,
           sum(n) OVER (PARTITION BY community_id)                  AS total_move_outs,
           row_number() OVER (PARTITION BY community_id
                              ORDER BY n DESC, move_out_reason)     AS rk
    FROM move_outs
)
SELECT
    community_id,
    rk                                        AS rank,
    move_out_reason,
    n                                         AS move_outs,
    round(100.0 * n / total_move_outs, 1)     AS pct_of_move_outs
FROM ranked
WHERE rk <= 3
ORDER BY community_id, rk;

-- ---------------------------------------------------------------------------
-- 3. Incident rate per 100 resident-days by community and care level.
--    Numerator: incidents (care level = resident's level in the incident
--    month). Denominator: resident-days from the census fact.
-- ---------------------------------------------------------------------------
WITH resident_days AS (
    SELECT community_id, care_level_code, sum(resident_days) AS resident_days
    FROM gold.fact_resident_month
    GROUP BY community_id, care_level_code
),
incidents AS (
    SELECT community_id, care_level_code, count(*) AS incidents
    FROM gold.fact_incident
    GROUP BY community_id, care_level_code
)
SELECT
    rd.community_id,
    rd.care_level_code,
    coalesce(i.incidents, 0)  AS incidents,
    rd.resident_days,
    round(100.0 * coalesce(i.incidents, 0) / rd.resident_days, 3)
        AS incidents_per_100_resident_days
FROM resident_days rd
LEFT JOIN incidents i
  ON i.community_id = rd.community_id
 AND i.care_level_code = rd.care_level_code
ORDER BY rd.community_id, rd.care_level_code;
