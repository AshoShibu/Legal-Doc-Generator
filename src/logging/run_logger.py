"""
src/logging/run_logger.py — SQLite-backed run logger for the Maharashtra Legal Document
Generation System.

Environment variables:
  LOG_DB_PATH         Path to the SQLite database file (default: ./output/runs.db)
  LOG_RETENTION_DAYS  Number of days to retain run logs and output files (default: 30)
  OUTPUT_DIR          Root output directory (default: ./output)
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DB_PATH = os.environ.get("LOG_DB_PATH", "./output/runs.db")
_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", "30"))
_OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    timestamp        TEXT,
    pipeline_variant TEXT,
    llm_model        TEXT,
    input_hash       TEXT,
    cache_composition TEXT,
    output_hash      TEXT,
    output_path      TEXT,
    status           TEXT
);

CREATE TABLE IF NOT EXISTS agent_traces (
    run_id       TEXT,
    agent        TEXT,
    sequence_num INTEGER,
    input_summary  TEXT,
    output_summary TEXT,
    duration_ms  INTEGER,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
"""


def _get_connection() -> sqlite3.Connection:
    """Return a connection to the SQLite database, creating it if necessary."""
    db_path = Path(_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def log_run(
    pipeline_variant: str,
    llm_model: str,
    input_hash: str,
    cache_composition: dict | list,
    output_hash: str,
    output_path: str,
    status: str,
) -> str:
    """Insert a new run record and return the generated run_id.

    Parameters
    ----------
    pipeline_variant:
        One of "cag", "dense", "hybrid", "agentic".
    llm_model:
        LLM backend identifier, e.g. "llama3_8b".
    input_hash:
        SHA-256 (or similar) hex digest of the input document.
    cache_composition:
        Dict or list describing the Legal Cache / corpus used.
    output_hash:
        Hash of the generated output document.
    output_path:
        Filesystem path where the output was stored.
    status:
        "success" | "error" | "partial"

    Returns
    -------
    str
        The UUID run_id assigned to this run.
    """
    run_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    cache_json = json.dumps(cache_composition)

    with _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO runs
                (run_id, timestamp, pipeline_variant, llm_model,
                 input_hash, cache_composition, output_hash, output_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, timestamp, pipeline_variant, llm_model,
             input_hash, cache_json, output_hash, output_path, status),
        )

    # Purge stale entries on every log_run call (lightweight — only deletes old rows)
    purge_old_runs()

    return run_id


def get_run(run_id: str) -> dict | None:
    """Retrieve a full run record including its agent traces.

    Parameters
    ----------
    run_id:
        UUID string returned by :func:`log_run`.

    Returns
    -------
    dict | None
        A dict with all ``runs`` columns plus an ``agent_traces`` list,
        or ``None`` if the run_id is not found.
    """
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

        if row is None:
            return None

        result = dict(row)
        # Deserialise the JSON blob back to a Python object
        try:
            result["cache_composition"] = json.loads(result["cache_composition"])
        except (json.JSONDecodeError, TypeError):
            pass  # leave as raw string if unparseable

        traces = conn.execute(
            """
            SELECT agent, sequence_num, input_summary, output_summary, duration_ms
            FROM agent_traces
            WHERE run_id = ?
            ORDER BY sequence_num
            """,
            (run_id,),
        ).fetchall()

        result["agent_traces"] = [dict(t) for t in traces]

    return result


def store_output(
    run_id: str,
    pipeline_variant: str,
    document_type: str,
    files: dict[str, bytes],
) -> str:
    """Write output files to ``output/{pipeline_variant}/{document_type}/{run_id}/``.

    Parameters
    ----------
    run_id:
        UUID string identifying the run.
    pipeline_variant:
        Pipeline name used as the first path segment (e.g. "cag").
    document_type:
        Document type used as the second path segment (e.g. "sale_deed").
    files:
        Mapping of filename → raw bytes to write.

    Returns
    -------
    str
        The absolute path of the directory where files were written.
    """
    output_dir = Path(_OUTPUT_DIR) / pipeline_variant / document_type / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    for filename, content in files.items():
        (output_dir / filename).write_bytes(content)

    return str(output_dir)


def log_agent_trace(
    run_id: str,
    agent: str,
    sequence_num: int,
    input_summary: str,
    output_summary: str,
    duration_ms: int,
) -> None:
    """Insert a single agent trace entry for a run.

    Parameters
    ----------
    run_id:
        UUID string of the parent run.
    agent:
        Agent name, e.g. "Issue_Agent".
    sequence_num:
        Ordinal position of this agent invocation within the run.
    input_summary:
        Short description of the agent's input.
    output_summary:
        Short description of the agent's output.
    duration_ms:
        Wall-clock duration of the agent invocation in milliseconds.
    """
    with _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO agent_traces
                (run_id, agent, sequence_num, input_summary, output_summary, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, agent, sequence_num, input_summary, output_summary, duration_ms),
        )


def purge_old_runs() -> None:
    """Delete run log entries and output directories older than LOG_RETENTION_DAYS.

    This is called automatically by :func:`log_run` on every invocation.
    It can also be called manually for maintenance.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)
    ).isoformat()

    with _get_connection() as conn:
        # Collect run IDs and output paths before deletion
        old_runs = conn.execute(
            "SELECT run_id, output_path FROM runs WHERE timestamp < ?",
            (cutoff,),
        ).fetchall()

        if not old_runs:
            return

        old_ids = [r["run_id"] for r in old_runs]
        placeholders = ",".join("?" * len(old_ids))

        # Delete child rows first (FK constraint)
        conn.execute(
            f"DELETE FROM agent_traces WHERE run_id IN ({placeholders})", old_ids
        )
        conn.execute(
            f"DELETE FROM runs WHERE run_id IN ({placeholders})", old_ids
        )

    # Remove output directories for purged runs
    for row in old_runs:
        output_path = row["output_path"]
        if output_path and Path(output_path).exists():
            try:
                shutil.rmtree(output_path)
            except OSError:
                pass  # best-effort; do not crash the logger
