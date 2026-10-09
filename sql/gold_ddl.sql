-- ===========================================================================
-- Pinewood Gold layer: star schema read by Power BI.
-- Rebuilt from Silver on every pipeline run.
--
-- Fact table grains:
--   fact_resident_month    : one row per resident per calendar month active.
--                            resident_days is the denominator for occupancy
--                            and incident-rate measures.
--   fact_incident          : one row per reported incident.
--   fact_lease             : one row per lease (current version).
--   fact_shift             : one row per staffing shift worked.
--   fact_review            : one row per Google review.
--   fact_lead              : one row per CRM lead with its funnel dates.
--   fact_care_level_change : one row per care-level change event.
--   fact_occupancy_month   : one row per community per month (pre-aggregated
--                            unit-day occupancy).
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS gold;

-- ------------------------------------------------------------- dimensions
CREATE OR REPLACE TABLE gold.dim_date (
    date_key         DATE NOT NULL,
    year             INTEGER,
    quarter          INTEGER,
    month            INTEGER,
    month_name       VARCHAR,
    month_start      DATE,
    day_of_month     INTEGER,
    day_name         VARCHAR,
    iso_week         INTEGER,
    is_weekend       BOOLEAN
);

CREATE OR REPLACE TABLE gold.dim_community (
    community_id     VARCHAR NOT NULL,
    community_name   VARCHAR,
    state            VARCHAR,   -- RLS: Community Executive Director key
    region           VARCHAR    -- RLS: Regional Director key
);

CREATE OR REPLACE TABLE gold.dim_resident (
    resident_id      VARCHAR NOT NULL,
    first_name       VARCHAR,
    last_name        VARCHAR,
    full_name        VARCHAR,
    dob              DATE,
    gender           VARCHAR
);

CREATE OR REPLACE TABLE gold.dim_unit (
    unit_id          VARCHAR NOT NULL,
    community_id     VARCHAR,
    unit_type        VARCHAR,   -- IL / AL / MC
    list_rent        DECIMAL(10,2)
);

CREATE OR REPLACE TABLE gold.dim_care_level (
    care_level_code  VARCHAR NOT NULL,
    care_level_name  VARCHAR,
    sort_order       INTEGER
);

-- ------------------------------------------------------------------ facts
CREATE OR REPLACE TABLE gold.fact_resident_month (
    resident_id      VARCHAR,
    community_id     VARCHAR,
    care_level_code  VARCHAR,
    month_start      DATE,
    resident_days    INTEGER,   -- days active within the month
    acuity_score     INTEGER
);

CREATE OR REPLACE TABLE gold.fact_incident (
    incident_id      VARCHAR,
    incident_date    DATE,
    resident_id      VARCHAR,
    community_id     VARCHAR,
    care_level_code  VARCHAR,   -- resident's level in the incident month
    incident_type    VARCHAR,
    severity         INTEGER,
    reported_by      VARCHAR
);

CREATE OR REPLACE TABLE gold.fact_lease (
    lease_id         VARCHAR,
    resident_id      VARCHAR,
    unit_id          VARCHAR,
    community_id     VARCHAR,
    move_in_date     DATE,
    move_out_date    DATE,
    move_out_reason  VARCHAR,
    monthly_rate     DECIMAL(10,2)
);

CREATE OR REPLACE TABLE gold.fact_shift (
    shift_id         VARCHAR,
    shift_date       DATE,
    community_id     VARCHAR,
    employee_id      VARCHAR,
    role             VARCHAR,
    hours_worked     DOUBLE,
    hourly_rate      DOUBLE,
    labor_cost       DOUBLE
);

CREATE OR REPLACE TABLE gold.fact_review (
    review_id          VARCHAR,
    review_date        DATE,
    community_id       VARCHAR,
    rating             INTEGER,
    has_response       BOOLEAN,
    response_lag_days  INTEGER
);

CREATE OR REPLACE TABLE gold.fact_lead (
    lead_id          VARCHAR,
    created_date     DATE,
    community_id     VARCHAR,
    lead_source      VARCHAR,
    tour_date        DATE,
    deposit_date     DATE,
    move_in_date     DATE,
    status           VARCHAR,
    lost_reason      VARCHAR
);

CREATE OR REPLACE TABLE gold.fact_care_level_change (
    resident_id          VARCHAR,
    change_date          DATE,
    previous_level_code  VARCHAR,
    new_level_code       VARCHAR,
    reason               VARCHAR
);

CREATE OR REPLACE TABLE gold.fact_occupancy_month (
    community_id        VARCHAR,
    month_start         DATE,
    total_units         INTEGER,
    available_unit_days INTEGER,   -- units in month snapshot x days in month
    resident_days       INTEGER,   -- census days from resident snapshots
    occupancy_pct       DOUBLE     -- resident_days / available_unit_days
);
