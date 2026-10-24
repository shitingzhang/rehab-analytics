"""
03_execute_sql.py
Runs every query in sql/analytical_queries.sql against data/processed/irf_analytics.db,
prints each result, and exports it to data/processed/sql_outputs/query_NN.csv.

The SQL file is the single source of truth (commit it to GitHub). Titles are read from the
"-- Query NN: ..." comment above each statement, so reordering queries never mislabels them.
Run from the project root:  python src/03_execute_sql.py
"""
import os
import re
import sqlite3
from typing import List, Tuple

import pandas as pd

pd.set_option("display.max_columns", 30)
pd.set_option("display.width", 1000)

DB_PATH = "data/processed/irf_analytics.db"
SQL_PATH = "sql/analytical_queries.sql"
SQL_OUT_DIR = "data/processed/sql_outputs"


def parse_queries(sql_text: str) -> List[Tuple[str, str]]:
    """Return [(title, statement)]. Uses sqlite3.complete_statement, so semicolons inside
    strings or comments cannot split a query."""
    queries, title, buf = [], None, []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            m = re.match(r"--\s*(Query\s*\d+.*)", stripped, flags=re.I)
            if m and not buf:
                title = m.group(1).strip()
            continue
        if not stripped and not buf:
            continue
        buf.append(line)
        statement = "\n".join(buf)
        if sqlite3.complete_statement(statement):
            queries.append((title or f"Query {len(queries) + 1}", statement.strip()))
            buf, title = [], None
    return queries


def execute_sql_analytics() -> None:
    for path, hint in [(DB_PATH, "Run 02_build_database.py first."), (SQL_PATH, "Put analytical_queries.sql in sql/.")]:
        if not os.path.exists(path):
            raise SystemExit(f"{path} not found. {hint}")
    os.makedirs(SQL_OUT_DIR, exist_ok=True)

    with open(SQL_PATH, "r", encoding="utf-8") as f:
        queries = parse_queries(f.read())

    print("=" * 88)
    print("EXECUTING INPATIENT REHABILITATION FACILITY SQL ANALYTICS")
    print("=" * 88)

    ok, failed = 0, []
    conn = sqlite3.connect(DB_PATH)
    try:
        for idx, (title, query) in enumerate(queries, start=1):
            print(f"\n>>> {title}\n" + "-" * 88)
            try:
                df = pd.read_sql_query(query, conn)
            except Exception as e:                      # one broken query must not hide the others
                print(f"FAILED: {e}")
                failed.append(title)
                continue
            print(df.to_string(index=False))
            df.to_csv(f"{SQL_OUT_DIR}/query_{idx:02d}.csv", index=False)
            ok += 1
    finally:
        conn.close()

    print("\n" + "=" * 88)
    print(f"{ok} of {len(queries)} queries executed and exported to {SQL_OUT_DIR}/")
    if failed:
        print("Failed:", *failed, sep="\n  - ")
    print("=" * 88)


if __name__ == "__main__":
    execute_sql_analytics()