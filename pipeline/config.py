"""Central configuration for the Pinewood medallion pipeline.

Every mapping that encodes a business decision lives here so it can be
reviewed and changed without touching transform logic.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DATA_DIR = Path(r"C:\Users\pulkitnehra\VSCode\Pinewood_Dataset\candidate_package\data")
DEFAULT_DB_PATH = PROJECT_ROOT / "warehouse" / "pinewood.duckdb"

# The eight source tables. Bronze discovers files named {table}_{YYYY}_{MM}.csv.
SOURCE_TABLES = [
    "pcc_residents",
    "pcc_incidents",
    "pcc_care_history",
    "yardi_units",
    "yardi_leases",
    "adp_shifts",
    "gbp_reviews",
    "hubspot_leads",
]

# Canonical care levels. The residents files use nine different spellings;
# everything is normalized to the three codes below in Silver.
CARE_LEVEL_MAP = {
    "il": "IL",
    "independent": "IL",
    "independent living": "IL",
    "al": "AL",
    "assisted": "AL",
    "assisted living": "AL",
    "mc": "MC",
    "memory": "MC",
    "memory care": "MC",
}

CARE_LEVELS = [
    ("IL", "Independent Living", 1),
    ("AL", "Assisted Living", 2),
    ("MC", "Memory Care", 3),
]

# Pinewood operates C001-C014. Anything else (C905, C934, ... appear in the
# Yardi unit exports) is treated as a junk/test record and quarantined.
VALID_COMMUNITIES = [f"C{n:03d}" for n in range(1, 15)]

# No community master file is provided. The data dictionary says to derive or
# hard-code it; nothing in the data reveals which community sits in which
# state, so this assignment is an ASSUMPTION to confirm with the client.
# Regions follow the assessment's RLS definition:
#   OR = Pacific Northwest, AZ = Southwest, TX = South.
COMMUNITY_STATE = {
    "C001": "OR", "C002": "OR", "C003": "OR", "C004": "OR", "C005": "OR",
    "C006": "AZ", "C007": "AZ", "C008": "AZ", "C009": "AZ",
    "C010": "TX", "C011": "TX", "C012": "TX", "C013": "TX", "C014": "TX",
}

STATE_REGION = {
    "OR": "Pacific Northwest",
    "AZ": "Southwest",
    "TX": "South",
}

# Clinical acuity is documented as 1 (low) to 10 (high). Values outside the
# range (-5, 50, 99 all occur) are nulled and logged, never silently kept.
ACUITY_MIN, ACUITY_MAX = 1, 10

# dim_date span: covers the earliest move-in/admit dates in the data (2021)
# through the end of the export window year.
DIM_DATE_START = "2021-01-01"
DIM_DATE_END = "2025-12-31"
