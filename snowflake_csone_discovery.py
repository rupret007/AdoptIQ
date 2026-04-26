#!/usr/bin/env python3
"""
Snowflake CSOne Data Discovery Script

Discovers whether CSOne (support case) data exists in Snowflake.

Run from project root:
  python snowflake_csone_discovery.py

Credentials: Loaded from secrets.env, .env, or _bundled_secrets (same as AdoptIQ app).
Supports: SNOWFLAKE_PASSWORD (direct) or Keeper auth.

Output: Report of tables that may contain CSOne/support case data, schema, and sample counts.
"""
import os
import sys
from pathlib import Path

# Load env before config
from dotenv import load_dotenv
load_dotenv()
load_dotenv(Path.home() / "Library" / "Application Support" / "AdoptIQ" / ".env")
if sys.platform == "win32":
    load_dotenv(Path(os.environ.get("APPDATA", str(Path.home()))) / "AdoptIQ" / ".env")

# Also load from secrets.env and _bundled_secrets (same as app)
try:
    load_dotenv(Path(__file__).parent / "secrets.env")
except Exception:
    pass
try:
    import _bundled_secrets
    if hasattr(_bundled_secrets, "get_secrets"):
        os.environ.update(_bundled_secrets.get_secrets())
except ImportError:
    pass

import re

# Round 7 / Phase 2.6: bound the table-name fragments we are allowed
# to interpolate into raw SQL.  Snowflake table identifiers are
# ``catalog.schema.name`` and each part is alphanumeric/underscore.
# Rows from ``information_schema.tables`` are normally clean, but they
# come from a remote system and the discovery script previously
# embedded them verbatim into ``f"SELECT COUNT(*) FROM {full_name}"``,
# which is a textbook SQL-injection sink if the catalog ever exposes a
# row with a quoted identifier or a stray semicolon.  We now reject
# anything that does not match the strict pattern.
_TABLE_PART_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_safe_table_identifier(full_name: str) -> bool:
    """Return True only when ``full_name`` is a 3-part dotted identifier
    whose every component matches the conservative pattern above."""
    if not isinstance(full_name, str) or not full_name:
        return False
    parts = full_name.split(".")
    if len(parts) != 3:
        return False
    return all(_TABLE_PART_RE.match(p or "") for p in parts)


def _safe_or_skip(full_name: str) -> bool:
    if _is_safe_table_identifier(full_name):
        return True
    print(
        f"  SKIP: refusing to interpolate non-conforming table name "
        f"into SQL: {full_name!r}"
    )
    return False


def main():
    try:
        import snowflake.connector
    except ImportError:
        print("ERROR: snowflake-connector-python not installed. Run: pip install snowflake-connector-python")
        sys.exit(1)

    # Use same connection as AdoptIQ app (supports password or Keeper)
    from adoptiq_backend import _connect_with_keeper

    print("=" * 70)
    print("Snowflake CSOne Data Discovery")
    print("=" * 70)
    print()

    ctx = None
    try:
        ctx = _connect_with_keeper()
        print("Connected to Snowflake successfully.\n")
    except Exception as e:
        print(f"ERROR: Failed to connect to Snowflake: {e}")
        sys.exit(1)

    cur = ctx.cursor()
    results = []

    # 1. List all tables in CX_DB and EDW that might contain case/support data
    keywords = ["case", "support", "csone", "tac", "sr", "incident", "ticket", "bems"]
    tables_sql = """
    SELECT table_catalog, table_schema, table_name, row_count
    FROM information_schema.tables
    WHERE table_catalog IN ('CX_DB', 'EDW_SALES_ETL_DB')
      AND table_type = 'BASE TABLE'
      AND (
        LOWER(table_name) LIKE '%case%'
        OR LOWER(table_name) LIKE '%support%'
        OR LOWER(table_name) LIKE '%csone%'
        OR LOWER(table_name) LIKE '%tac%'
        OR LOWER(table_name) LIKE '%sr%'
        OR LOWER(table_name) LIKE '%incident%'
        OR LOWER(table_name) LIKE '%ticket%'
        OR LOWER(table_name) LIKE '%bems%'
      )
    ORDER BY table_catalog, table_schema, table_name
    """
    try:
        cur.execute(tables_sql)
        tables = cur.fetchall()
    except Exception as e:
        print(f"Note: information_schema.tables query failed (may lack access): {e}")
        tables = []

    print("## 1. Tables with case/support-related names")
    print("-" * 70)
    if not tables:
        print("No tables found matching: case, support, csone, tac, sr, incident, ticket, bems")
    else:
        for row in tables:
            catalog, schema, name, row_count = row[0], row[1], row[2], row[3] if len(row) > 3 else None
            full_name = f"{catalog}.{schema}.{name}"
            # Round 2 / Phase 5.8: ``information_schema.tables.row_count``
            # in Snowflake is maintained asynchronously and is often
            # stale or NULL; previously this was printed without
            # caveat which led ops to treat it as authoritative.  Label
            # it as approximate and (best-effort) confirm with an
            # exact ``COUNT(*)`` for the discovery report.
            if row_count is None:
                rc = " (rows: unknown — information_schema.row_count is NULL)"
            else:
                rc = f" (rows: ~{row_count} approximate per information_schema)"
                # Round 7 / Phase 2.6: validate the discovered identifier
                # against the strict pattern before interpolating it
                # into SELECT COUNT(*).
                if _safe_or_skip(full_name):
                    try:
                        cur.execute(f"SELECT COUNT(*) FROM {full_name}")
                        exact = cur.fetchone()[0]
                        rc = f" (rows: {exact:,} exact via COUNT(*); information_schema reported ~{row_count})"
                    except Exception as _exact_err:
                        rc += f"; exact COUNT(*) failed: {_exact_err}"
                else:
                    rc += "; exact COUNT(*) skipped (identifier failed allowlist regex)"
            print(f"  {full_name}{rc}")
    print()

    # 2. Explicitly check SUPPORT_CASES (used by AdoptIQ as CSOne fallback)
    support_tables = [
        "CX_DB.CX_SWSSBST_BR.SUPPORT_CASES",
    ]
    print("## 2. SUPPORT_CASES table (AdoptIQ fallback when no CSOne Excel)")
    print("-" * 70)
    for full_name in support_tables:
        # Round 7 / Phase 2.6: even though ``support_tables`` is a
        # hardcoded literal today, route every interpolation through
        # the same allowlist regex so a future maintainer cannot
        # accidentally extend the list with a non-conforming entry.
        if not _safe_or_skip(full_name):
            continue
        parts = full_name.split(".")
        if len(parts) != 3:
            continue
        db, schema, table = parts
        try:
            cur.execute(f"SELECT COUNT(*) FROM {full_name}")
            count = cur.fetchone()[0]
            print(f"  {full_name}: {count:,} rows")
        except Exception as e:
            print(f"  {full_name}: NOT FOUND or NO ACCESS - {e}")
    print()

    # 3. Get columns for SUPPORT_CASES (if it exists)
    print("## 3. SUPPORT_CASES schema (columns)")
    print("-" * 70)
    try:
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_catalog = 'CX_DB'
              AND table_schema = 'CX_SWSSBST_BR'
              AND table_name = 'SUPPORT_CASES'
            ORDER BY ordinal_position
        """)
        cols = cur.fetchall()
        if cols:
            for cname, dtype in cols:
                print(f"  {cname}: {dtype}")
        else:
            print("  (No columns returned - table may not exist or no access)")
    except Exception as e:
        print(f"  Error: {e}")
    print()

    # 4. Compare with CSOne Excel columns (what AdoptIQ expects from Excel)
    csone_excel_cols = {
        "Date/Time Opened", "SR Number", "Case Number", "Title", "Problem Description",
        "Severity", "Status", "Customer Name", "Subscription ID", "Transaction ID",
        "bemscsc_refs", "Product", "Technology", "Sub Technology"
    }
    print("## 4. CSOne Excel columns (AdoptIQ expects from upload) vs SUPPORT_CASES")
    print("-" * 70)
    try:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_catalog = 'CX_DB'
              AND table_schema = 'CX_SWSSBST_BR'
              AND table_name = 'SUPPORT_CASES'
        """)
        sf_cols = {r[0] for r in cur.fetchall()}
        overlap = csone_excel_cols & sf_cols
        only_excel = csone_excel_cols - sf_cols
        only_sf = sf_cols - csone_excel_cols
        print("  Columns in BOTH (Excel + Snowflake):", sorted(overlap) if overlap else "(none)")
        print("  Only in CSOne Excel:", sorted(only_excel)[:10], "..." if len(only_excel) > 10 else sorted(only_excel))
        print("  Only in SUPPORT_CASES:", sorted(only_sf)[:15], "..." if len(only_sf) > 15 else sorted(only_sf))
    except Exception as e:
        print(f"  Error: {e}")
    print()

    # 5. Sample a few rows from SUPPORT_CASES (if exists and has data)
    print("## 5. Sample rows from SUPPORT_CASES (first 3)")
    print("-" * 70)
    try:
        cur.execute("""
            SELECT * FROM CX_DB.CX_SWSSBST_BR.SUPPORT_CASES
            LIMIT 3
        """)
        rows = cur.fetchall()
        col_names = [c[0] for c in cur.description] if cur.description else []
        if rows:
            for i, row in enumerate(rows):
                print(f"  Row {i+1}:")
                for j, c in enumerate(col_names):
                    val = row[j] if j < len(row) else ""
                    if val is not None and len(str(val)) > 60:
                        val = str(val)[:60] + "..."
                    print(f"    {c}: {val}")
        else:
            print("  (Table exists but has 0 rows)")
    except Exception as e:
        print(f"  Error: {e}")
    print()

    # 6. Search for any table with "CSONE" in the name
    print("## 6. Tables with 'CSONE' in name")
    print("-" * 70)
    try:
        cur.execute("""
            SELECT table_catalog, table_schema, table_name
            FROM information_schema.tables
            WHERE table_catalog IN ('CX_DB', 'EDW_SALES_ETL_DB')
              AND LOWER(table_name) LIKE '%csone%'
            ORDER BY table_catalog, table_schema, table_name
        """)
        csone_tables = cur.fetchall()
        if csone_tables:
            for row in csone_tables:
                print(f"  {row[0]}.{row[1]}.{row[2]}")
        else:
            print("  No tables with 'csone' in name found.")
    except Exception as e:
        print(f"  Error: {e}")
    print()

    # 7. Databases/schemas we have access to
    print("## 7. Accessible databases (summary)")
    print("-" * 70)
    try:
        cur.execute("SHOW DATABASES")
        dbs = [r[1] for r in cur.fetchall() if r]
        cx = [d for d in dbs if d and 'CX' in d.upper()]
        edw = [d for d in dbs if d and 'EDW' in d.upper()]
        print("  CX_* databases:", cx[:10] if len(cx) > 10 else cx)
        print("  EDW_* databases:", edw[:10] if len(edw) > 10 else edw)
    except Exception as e:
        print(f"  Error: {e}")

    if cur:
        cur.close()
    if ctx:
        ctx.close()

    print()
    print("=" * 70)
    print("Discovery complete.")
    print("=" * 70)

if __name__ == "__main__":
    main()
