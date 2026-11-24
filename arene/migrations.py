"""Migrations versionnées de la base de données, appliquées au démarrage d'arene-web."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (
        1,
        "initial_schema",
        [
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                github_id INTEGER UNIQUE NOT NULL,
                github_login TEXT NOT NULL,
                avatar_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                is_system INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                zip_key TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0,
                is_reference INTEGER NOT NULL DEFAULT 0,
                is_anchor INTEGER NOT NULL DEFAULT 0,
                elo REAL NOT NULL DEFAULT 1000.0,
                margin REAL NOT NULL DEFAULT 999.0,
                provisional INTEGER NOT NULL DEFAULT 1,
                games_played INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                draws INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                placement_done INTEGER NOT NULL DEFAULT 0,
                placement_total INTEGER NOT NULL DEFAULT 5,
                reject_reason TEXT NOT NULL DEFAULT '',
                submitted_at TEXT NOT NULL,
                validated_at TEXT,
                ranked_at TEXT,
                replaced_at TEXT,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_agents_account ON agents(account_id)",
            "CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status, submitted_at)",
            "CREATE INDEX IF NOT EXISTS idx_agents_active_elo ON agents(is_active, elo)",
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                priority INTEGER NOT NULL,
                status TEXT NOT NULL,
                focus_agent_id TEXT REFERENCES agents(id) ON DELETE CASCADE,
                opponent_agent_id TEXT REFERENCES agents(id) ON DELETE CASCADE,
                payload_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                lease_worker TEXT,
                lease_expires_at TEXT,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                duration_s REAL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, priority, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_focus ON jobs(focus_agent_id, status)",
            """
            CREATE TABLE IF NOT EXISTS matches (
                id TEXT PRIMARY KEY,
                job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
                job_type TEXT NOT NULL,
                seed INTEGER NOT NULL,
                left_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                right_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                outcome TEXT NOT NULL,
                end_frame INTEGER NOT NULL,
                left_health REAL NOT NULL,
                right_health REAL NOT NULL,
                left_mean_ms REAL NOT NULL DEFAULT 0.0,
                left_max_ms REAL NOT NULL DEFAULT 0.0,
                right_mean_ms REAL NOT NULL DEFAULT 0.0,
                right_max_ms REAL NOT NULL DEFAULT 0.0,
                left_exceptions INTEGER NOT NULL DEFAULT 0,
                right_exceptions INTEGER NOT NULL DEFAULT 0,
                failure_side TEXT NOT NULL DEFAULT '',
                failure_reason TEXT NOT NULL DEFAULT '',
                game_version TEXT NOT NULL,
                replay_key TEXT NOT NULL DEFAULT '',
                replay_deleted INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_matches_agents ON matches(is_active, left_agent_id, right_agent_id)",
            "CREATE INDEX IF NOT EXISTS idx_matches_job ON matches(job_id)",
            """
            CREATE TABLE IF NOT EXISTS elo_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                elo REAL NOT NULL,
                margin REAL NOT NULL,
                games_played INTEGER NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_elo_hist_agent ON elo_history(agent_id, id)",
            """
            CREATE TABLE IF NOT EXISTS ip_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                event_type TEXT NOT NULL,
                account_id TEXT,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_ip_events_lookup ON ip_events(ip_address, event_type, created_at)",
            """
            CREATE TABLE IF NOT EXISTS runtime_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ],
    ),
]


def apply_migrations(engine: Engine) -> None:
    """Applique toutes les migrations SQL non encore enregistrées."""
    is_postgres = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
        )
        applied = {
            row[0]
            for row in conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
        }
        now = datetime.now(timezone.utc).isoformat()
        for version, name, statements in MIGRATIONS:
            if version in applied:
                continue
            for stmt in statements:
                sql = stmt
                if is_postgres:
                    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                conn.execute(text(sql))
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (:v, :n, :t)"
                ),
                {"v": version, "n": name, "t": now},
            )
