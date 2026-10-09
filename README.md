# Pinewood Senior Living — Data Platform

Medallion (Bronze / Silver / Gold) pipeline that turns six months of raw CSV
exports from five source systems (PointClickCare, Yardi, ADP, Google Business
Profile, HubSpot) into a star-schema warehouse ready for Power BI.

**Walkthrough video:** _[link goes here — record and paste before submitting]_

```
pipeline/        Python ingestion code (Bronze -> Silver -> Gold -> Parquet export)
sql/             Gold DDL (with fact grains) and the three analysis queries
powerbi/         Power BI report in source-controlled PBIP format (+ .pbix copy)
communication/   email_to_karen.md
warehouse/       generated DuckDB file + Parquet exports (gitignored)
```

## Quick start

```bash
pip install -r pipeline/requirements.txt       # duckdb (only dependency)
python pipeline/run_pipeline.py --data-dir <path-to-csv-folder>
```

`--data-dir` defaults to the candidate package data folder; `--db` defaults to
`warehouse/pinewood.duckdb`. The run ends with a summary log of what was
processed, loaded, and quarantined per table.

The pipeline is **rerunnable without duplicating data**: Bronze replaces rows
per source file, Silver and Gold are rebuilt from Bronze on every run.
Running it twice produces identical row counts (verified).

## Architecture

```
CSVs ──> bronze.*  raw VARCHAR + ingestion metadata (_source_file, _period,
         │         _row_num, _run_id, _ingested_at). Nothing cleaned.
         │         Schema drift absorbed via dynamic ALTER TABLE ADD COLUMN.
         ▼
         silver.*  typed, cleaned, deduplicated. Canonical care levels
         │         (IL/AL/MC), one date format, duplicates resolved. Every
         │         dropped row / altered value logged to meta.quarantine.
         ▼
         gold.*    star schema for Power BI: 5 dimensions, 8 facts
                   (grains documented in sql/gold_ddl.sql).

meta.run_log / meta.quarantine — per-run audit of processed vs rejected rows.
```

Storage is a single DuckDB file (`warehouse/pinewood.duckdb`) with one schema
per layer: `bronze`, `silver`, `gold`, `meta`.

| File | Responsibility |
|---|---|
| `pipeline/run_pipeline.py` | entrypoint, orchestration, run summary |
| `pipeline/config.py` | care-level map, community/state map, valid ranges |
| `pipeline/bronze.py` | raw ingestion, drift handling, per-file idempotency |
| `pipeline/silver.py` | cleaning/typing/dedup rules + quarantine logging |
| `pipeline/gold.py` | star-schema population |
| `pipeline/quality.py` | run log, quarantine, summary printing |
| `sql/gold_ddl.sql` | Gold DDL with the grain of each fact table |

## Anomalies Found

1. **Nine spellings of care level** in the residents files (`IL`,
   `Independent`, `Independent Living`, same for AL/MC). *Fixed in pipeline*:
   normalized to canonical IL/AL/MC through a mapping table; unmapped values
   would be nulled and logged.
2. **`pcc_residents_2025_03.csv` switches to MM/DD/YYYY dates** while every
   other file is ISO. *Fixed in pipeline*: `meta.parse_date()` tries ISO then
   US format; all 683 March rows parse.
3. **`pcc_residents_2025_04.csv` adds a `mobility_status` column** (schema
   drift). *Fixed in pipeline*: Bronze adds new columns dynamically; other
   months carry NULL. The column is kept in Silver since it looks like real
   clinical data — worth confirming with the client whether it will persist.
4. **ADP `hourly_rate` contains the entire role→rate map as a serialized
   dict in every row** of all six files (export bug). *Fixed in pipeline*:
   the rate for the row's own role is extracted; a plain-numeric fallback
   handles a future corrected export. 0 of 68,071 rows failed to resolve.
5. **Five phantom communities (C905, C934, C936, C951, C969)** appear in the
   Yardi unit snapshots — one unit each, every month, outside the documented
   C001–C014 range. *Quarantined* (30 rows): they look like test records, but
   I would raise them to the client before permanently excluding.
6. **Out-of-range acuity scores** (−5, 50, 99 — 18 rows; valid range is
   1–10). *Fixed in pipeline*: nulled and logged rather than guessed, since
   acuity feeds clinical KPIs.
7. **Near-duplicate residents within one month** (Feb: R01001, R01146,
   R01188 each appear twice — same person, first name misspelled, different
   community). *Fixed in pipeline*: keep the row whose community matches the
   resident's most frequent community across all snapshots; quarantine the
   other. Root cause (cross-community record copies) is worth raising.
8. **Duplicate lease rows across monthly files** (44): a lease legitimately
   appears in its creation month's file and again in its move-out month's
   file, with identical data. *Fixed in pipeline*: deduplicated on lease_id,
   latest file wins. One duplicated HubSpot lead_id handled the same way.
9. **No community master data** exists in any source. `gold.dim_community`
   (state, region) is hard-coded in `pipeline/config.py` as a **documented
   assumption** — the OR/AZ/TX split cannot be derived from the data and must
   be confirmed with the client.

Verified clean (checked, no action needed): referential integrity
(incidents/leases/care history all resolve to known residents/units),
severity 1–5, ratings 1–5, shift hours 4–12, funnel date ordering
(created ≤ tour ≤ deposit ≤ move-in), move-out ≥ move-in, response ≥ review.

## Part 2 — Data modeling and SQL

DDL: [sql/gold_ddl.sql](sql/gold_ddl.sql). Analysis queries (monthly
occupancy by community, top-3 move-out reasons by community as % of
move-outs, incident rate per 100 resident-days by community and care level):
[sql/queries.sql](sql/queries.sql).

### Fact table grains

| Fact | Grain |
|---|---|
| `fact_resident_month` | one row per resident per month active (census; `resident_days` is the denominator for occupancy and incident rates) |
| `fact_incident` | one row per reported incident |
| `fact_lease` | one row per lease (current version after dedup) |
| `fact_shift` | one row per staffing shift worked |
| `fact_review` | one row per Google review |
| `fact_lead` | one row per CRM lead with its funnel dates |
| `fact_care_level_change` | one row per care-level change event |
| `fact_occupancy_month` | one row per community per month (pre-aggregated occupancy) |

### Modeling notes

- **Occupancy** = census resident-days ÷ available unit-days. The lease
  export only contains leases created or ended inside the window, so it
  cannot reconstruct the full census; the resident snapshots can.
- `fact_resident_month.resident_days` counts days active in the month
  (admit and discharge days inclusive, clipped to the month).
- `fact_incident.care_level_code` is the resident's care level in the
  incident month, enabling incident rate per 100 resident-days by care level.

## Part 3 — Power BI (PBIP, source-controlled)

The report lives in [powerbi/](powerbi/) in **PBIP format** (plain-text TMDL
semantic model + PBIR report definition, friendly to git diff/review):

```
powerbi/
  Pinewood.pbip                 open this in Power BI Desktop
  Pinewood.SemanticModel/       model as TMDL (tables, relationships, roles)
  Pinewood.Report/              report as PBIR (pages, visuals)
  generate_tables.py            regenerates table TMDL from the warehouse schema
```

To open: run the pipeline first (it exports Gold to
`warehouse/export/*.parquet`), then open `powerbi/Pinewood.pbip` in Power BI
Desktop and refresh. On another machine, update the **GoldFolder** parameter
(Transform data > Manage parameters) to your local `warehouse\export\` path.
The model imports Parquet files rather than connecting to DuckDB directly so
no ODBC driver is required.

### Relationships

All relationships are **many-to-one from fact to dimension with single
(one-way) cross-filtering** — dimensions filter facts, never the reverse.
One-way keeps filter propagation unambiguous and makes RLS on
`dim_community` flow into every fact without bidirectional security risks.

- Every fact → `dim_community[community_id]` (m:1, single): community slicing + RLS entry point.
- Every fact → `dim_date[date_key]` on its event date (m:1, single);
  monthly facts join on their `month_start`. `fact_lease` has its **active**
  date relationship on `move_out_date` (the dashboard's churn questions) and
  an **inactive** one on `move_in_date` for `USERELATIONSHIP` when move-in
  analysis is needed.
- `fact_resident_month` / `fact_incident` → `dim_resident`, `dim_care_level` (m:1, single).
- `dim_unit[community_id]` → `dim_community` is kept **inactive**: the active
  path would create an ambiguous second route `dim_community → dim_unit →
  fact_lease` alongside the direct community relationship.

### DAX measures (the four required)

1. **Current Occupancy %** (`fact_occupancy_month`) — pins the calculation
   to the latest month in the data (`REMOVEFILTERS` on the date context,
   then filters `month_start` to the max), while still respecting community
   filters and RLS. Shows "where we are today" regardless of date slicers.
2. **Move-Out Rate % (Trailing 90D)** (`fact_lease`) — move-outs in the 90
   days ending at the last data month ÷ distinct residents active in the
   window's months. A share-of-residents churn rate; community/RLS context
   applies, date slicers are overridden because "trailing 90 days" anchors
   to the data's end, not the slicer.
3. **Incident Rate per 100 Resident-Days** (`fact_incident`) — COUNTROWS of
   incidents ÷ SUM of `resident_days` × 100. Both facts respond to the same
   community/date/care-level dimensions, so the ratio is valid at any slice.
4. **Occupancy % Prior Month / MoM Delta** (time intelligence) — `DATEADD`
   on the marked date table shifts the occupancy context back one month; the
   delta version shows momentum on the exec page.

Supporting measures: Occupancy %, Census (Active Residents), Current Census
(pinned to the latest data month, used on the KPI card), Resident Days,
Incidents, Move-Outs, Reviews, Avg Rating, Labor Hours, Labor Cost, Leads,
Lead Conversion %.

Note on opening fresh from git: PBIP stores no data cache, so Desktop shows a
"Refresh now" banner on first open — click it once and the model loads from
the Parquet exports.

### Row-level security

Both roles filter `dim_community`; single-direction relationships propagate
the filter to every fact.

- **Regional Director** — `dim_community[region] = "Pacific Northwest"`
  (regions per the brief: OR = Pacific Northwest, AZ = Southwest, TX = South).
- **Community Executive Director** — `dim_community[community_id] = "C001"`.

Test with *Modeling > View As* in Desktop. The roles use static example
values so View As works out of the box; production would use dynamic RLS
(`USERPRINCIPALNAME()` against a user-mapping table), noted in the model.

### Executive dashboard page

KPI cards (Current Occupancy %, Census, Incident Rate per 100 Resident-Days,
Avg Rating), occupancy trend by month, incident rate by community, move-out
reasons, and a community scorecard matrix (occupancy, census, incidents,
move-outs, rating, labor cost) with a community slicer — the questions a COO
asks weekly: where are we full, where are people getting hurt, why are
people leaving, and which communities need attention.
