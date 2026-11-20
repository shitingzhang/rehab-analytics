"""Run the descriptive queries in sql/analytical_queries.sql and export a CSV for each.

Use --show-current to locate the results of a successful latest run, or
--show-last-successful to retrieve the most recent completed export.
"""
import os
import re
import sqlite3
import json
import tempfile
import hashlib
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing, contextmanager
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
    if any(line.strip() for line in buf):
        raise ValueError("Incomplete SQL statement at end of file.")
    if not queries:
        raise ValueError("No SQL queries found.")
    ids = [query_id(title) for title, _ in queries]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate Query NN identifiers.")
    return queries


def query_id(title):
    return int(re.match(r"Query\s*(\d+)\b", title, flags=re.I).group(1))


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def writer_lock(root):
    lock = root / ".writer.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise RuntimeError("SQL publication is locked. Check for a running/interrupted writer before removing .writer.lock.") from exc
    try:
        with handle:
            json.dump({"pid": os.getpid(), "started_utc": utc_now()}, handle)
        yield
    finally:
        lock.unlink()


def write_status(status):
    """One replacement publishes both attempt status and the successful-batch pointer."""
    root = Path(SQL_OUT_DIR)
    with tempfile.TemporaryDirectory(dir=root, prefix=".status-") as tmp:
        temporary = Path(tmp) / "run_status.json"
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(status, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / "run_status.json")


def validate_batch(root, reference):
    """Pin one batch and reject incomplete, modified or out-of-root result files."""
    relative = reference.get("directory", "")
    if not isinstance(relative, str) or not re.fullmatch(r"runs/[A-Za-z0-9_-]+", relative):
        raise ValueError("Invalid batch directory")
    batch = (root / relative).resolve()
    if not batch.is_relative_to(root.resolve()):
        raise ValueError("Batch directory escapes output root")
    manifest_path = batch / "manifest.json"
    if file_digest(manifest_path) != reference.get("manifest_sha256"):
        raise ValueError("SQL batch manifest checksum mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1 or manifest.get("run_id") != batch.name:
        raise ValueError("Invalid SQL batch manifest")
    files = manifest.get("files", [])
    names = [item["filename"] for item in files]
    if not files or len(set(names)) != len(names):
        raise ValueError("Empty or duplicate batch outputs")
    if set(p.name for p in batch.iterdir()) != set(names) | {"manifest.json"}:
        raise ValueError("Unexpected or missing batch files")
    for item in files:
        name = item["filename"]
        if not re.fullmatch(r"query_[0-9]{2,}\.csv", name):
            raise ValueError("Invalid query output name")
        path = batch / name
        if path.resolve().parent != batch:
            raise ValueError("Query output escapes batch directory")
        if file_digest(path) != item["sha256"]:
            raise ValueError(f"SQL batch file checksum mismatch: {name}")
    return batch


def _result_from_status(root, status):
    reference = status.get("current_batch")
    if not isinstance(reference, dict):
        raise RuntimeError("No complete SQL batch has been published")
    return validate_batch(root, reference)


def current_result_dir(output_root=None):
    """Pin a validated batch only when the latest recorded SQL attempt succeeded."""
    root = Path(SQL_OUT_DIR if output_root is None else output_root)
    status = json.loads((root / "run_status.json").read_text(encoding="utf-8"))
    if status.get("status") != "success":
        raise RuntimeError("Latest SQL attempt is not successful; use explicit historical access for an older result.")
    return _result_from_status(root, status)


def last_successful_result_dir(output_root=None):
    """Return the last completed export, including after a later failed run."""
    root = Path(SQL_OUT_DIR if output_root is None else output_root)
    status = json.loads((root / "run_status.json").read_text(encoding="utf-8"))
    return _result_from_status(root, status)


def execute_sql_analytics() -> None:
    root = Path(SQL_OUT_DIR)
    root.mkdir(parents=True, exist_ok=True)
    with writer_lock(root):
        status_path = root / "run_status.json"
        previous = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        current = previous.get("current_batch")
        if current is not None:
            validate_batch(root, current)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ_") + uuid.uuid4().hex[:8]
        attempt = {"format_version": 1, "attempt_id": run_id, "started_utc": utc_now(),
                   "current_batch": current}
        write_status({**attempt, "status": "incomplete"})
        try:
            reference, ids = _execute_sql_analytics(run_id)
            write_status({**attempt, "status": "success", "completed_utc": utc_now(),
                          "query_ids": ids, "current_batch": reference})
        except BaseException as exc:
            try:
                write_status({**attempt, "status": "failed", "completed_utc": utc_now(),
                              "error": f"{type(exc).__name__}: {exc}",
                              "warning": "The current_batch pointer still selects the previous successful batch, if any."})
            except Exception as status_error:
                print(f"Could not record failure status: {status_error}; inspect run_status.json.", file=sys.stderr)
            raise
    print(f"\nPublished {len(ids)} queries as one complete batch: {root / reference['directory']}")


def _execute_sql_analytics(run_id):
    for path, hint in [(DB_PATH, "Run 02_build_database.py first."), (SQL_PATH, "Put analytical_queries.sql in sql/.")]:
        if not Path(path).is_file():
            raise FileNotFoundError(f"{path} not found. {hint}")
    db = Path(DB_PATH).resolve()
    if any(Path(str(db) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise RuntimeError("Database journal/WAL files exist; finish database writes before SQL export.")
    sql_bytes = Path(SQL_PATH).read_bytes()
    queries = parse_queries(sql_bytes.decode("utf-8"))
    root = Path(SQL_OUT_DIR)
    (root / "runs").mkdir(exist_ok=True)
    batch = root / "runs" / run_id
    print("Running facility queries")
    with tempfile.TemporaryDirectory(dir=root, prefix=".staging-") as staging:
        stage = Path(staging)
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()  # pin one read snapshot
            db_hash = file_digest(db)
            files = []
            for title, query in queries:
                print(f"\n>>> {title}\n" + "-" * 88)
                try:
                    frame = pd.read_sql_query(query, conn)
                except Exception as exc:
                    raise RuntimeError(f"{title} failed: {exc}") from exc
                print(frame.to_string(index=False))
                filename = f"query_{query_id(title):02d}.csv"
                frame.to_csv(stage / filename, index=False)
                files.append({"query_id": query_id(title), "title": title, "filename": filename,
                              "row_count": len(frame), "columns": list(frame.columns),
                              "sha256": file_digest(stage / filename)})
            if file_digest(db) != db_hash or Path(SQL_PATH).read_bytes() != sql_bytes:
                raise RuntimeError("SQL inputs changed during export; no batch was published")
        manifest = {"format_version": 1, "run_id": run_id, "completed_utc": utc_now(),
                    "database_sha256": db_hash, "sql_sha256": hashlib.sha256(sql_bytes).hexdigest(),
                    "script_sha256": file_digest(Path(__file__)),
                    "environment": {"python": sys.version.split()[0], "pandas": pd.__version__,
                                    "sqlite": sqlite3.sqlite_version}, "files": files}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        reference = {"directory": f"runs/{run_id}", "manifest_sha256": file_digest(stage / "manifest.json")}
        os.replace(stage, batch)
    validate_batch(root, reference)
    return reference, [query_id(title) for title, _ in queries]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    readers = parser.add_mutually_exclusive_group()
    readers.add_argument("--show-current", action="store_true", help="Print validated results only if the latest SQL attempt succeeded")
    readers.add_argument("--show-last-successful", action="store_true", help="Show the most recent completed SQL export")
    args = parser.parse_args()
    if args.show_current:
        print(current_result_dir())
    elif args.show_last_successful:
        print(last_successful_result_dir())
    else:
        execute_sql_analytics()
