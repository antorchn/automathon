"""Ordonnancement de la file d'attente, baux des workers, placements, affinage et rétention."""

from __future__ import annotations

import json
import random
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from arene.config import Settings
from arene.db import write_tx
from arene.elo import (
    EloEstimate,
    MatchRecord,
    choose_next_placement_opponent,
    choose_refinement_pair,
    estimate_single_agent_elo,
    solve_bradley_terry,
)
from arene.storage import FileStore
from arene.zip_validator import validate_agent_zip


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class SubmissionRejectedError(ValueError):
    """Erreur métier lors de la soumission (cooldown, file pleine, etc.)."""


def _build_zip_from_dir(agent_dir: Path) -> bytes:
    import io

    buf = io.BytesIO()
    name = agent_dir.name
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(agent_dir.rglob("*")):
            if not file_path.is_file():
                continue
            if "__pycache__" in file_path.parts or file_path.suffix == ".pyc":
                continue
            zf.write(file_path, f"{name}/{file_path.relative_to(agent_dir).as_posix()}")
    return buf.getvalue()


def _load_reference_zip_bytes(name: str, settings: Settings) -> bytes:
    zip_path = settings.reference_agents_dir / f"{name}.zip"
    if zip_path.is_file():
        return zip_path.read_bytes()
    repo_root = Path(__file__).resolve().parent.parent
    candidates = [
        repo_root / "sources" / "extracted" / "reference_agents" / f"{name}.zip",
        repo_root / "sources" / "extracted" / "kit" / "PythonAI" / "agents" / name,
        repo_root / "PythonAI" / "agents" / name,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand.read_bytes()
        if cand.is_dir() and (cand / "agent.py").is_file():
            return _build_zip_from_dir(cand)
    raise FileNotFoundError(f"Bot de référence introuvable pour {name}")


def seed_reference_agents(session: Session, store: FileStore, settings: Settings) -> None:
    """Enregistre les bots de référence (rush_bot, dummy_bot) s'ils ne sont pas encore en base."""
    now = _iso(_utcnow())
    with write_tx(session):
        acc = session.execute(
            text("SELECT id FROM accounts WHERE github_id = 0")
        ).fetchone()
        if acc is None:
            acc_id = "acc_system"
            session.execute(
                text(
                    """
                    INSERT INTO accounts (id, github_id, github_login, avatar_url, created_at, is_system)
                    VALUES (:id, 0, 'automathon', '', :now, 1)
                    """
                ),
                {"id": acc_id, "now": now},
            )
        else:
            acc_id = acc[0]

        for name in ("rush_bot", "dummy_bot"):
            existing = session.execute(
                text("SELECT id FROM agents WHERE is_reference = 1 AND name = :name"),
                {"name": name},
            ).fetchone()
            if existing is not None:
                continue
            zip_bytes = _load_reference_zip_bytes(name, settings)
            _, sha = validate_agent_zip(zip_bytes, max_mb=settings.submission_max_mb)
            zip_key = f"agents/{sha}.zip"
            if not store.exists(zip_key):
                store.put_bytes(zip_key, zip_bytes)

            is_anchor = 1 if name == settings.anchor_agent_name else 0
            margin = 0.0 if is_anchor else 999.0
            provisional = 0 if is_anchor else 1
            agent_id = f"agent_ref_{name}"
            session.execute(
                text(
                    """
                    INSERT INTO agents (
                        id, account_id, name, zip_key, sha256, status, is_active,
                        is_reference, is_anchor, elo, margin, provisional,
                        games_played, wins, draws, losses, placement_done, placement_total,
                        reject_reason, submitted_at, validated_at, ranked_at, updated_at
                    ) VALUES (
                        :id, :acc, :name, :zip_key, :sha, 'ranked', 1,
                        1, :is_anchor, :elo, :margin, :prov,
                        0, 0, 0, 0, 0, 0,
                        '', :now, :now, :now, :now
                    )
                    """
                ),
                {
                    "id": agent_id,
                    "acc": acc_id,
                    "name": name,
                    "zip_key": zip_key,
                    "sha": sha,
                    "is_anchor": is_anchor,
                    "elo": settings.anchor_elo,
                    "margin": margin,
                    "prov": provisional,
                    "now": now,
                },
            )


def get_runtime_flag(session: Session, key: str, default: str) -> str:
    row = session.execute(
        text("SELECT value FROM runtime_config WHERE key = :k"),
        {"k": key},
    ).fetchone()
    return row[0] if row is not None else default


def set_runtime_flag(session: Session, key: str, value: str) -> None:
    now = _iso(_utcnow())
    with write_tx(session):
        session.execute(
            text(
                """
                INSERT INTO runtime_config (key, value, updated_at) VALUES (:k, :v, :t)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """
            ),
            {"k": key, "v": value, "t": now},
        )


def is_background_enabled(session: Session, settings: Settings) -> bool:
    raw = get_runtime_flag(
        session,
        "background_enabled",
        "true" if settings.background_enabled else "false",
    )
    return raw.lower() in ("1", "true", "yes", "oui")


def get_cooldown_remaining_seconds(session: Session, account_id: str, settings: Settings) -> int:
    if settings.cooldown_minutes <= 0:
        return 0
    row = session.execute(
        text(
            """
            SELECT submitted_at FROM agents
            WHERE account_id = :acc AND is_reference = 0
            ORDER BY submitted_at DESC LIMIT 1
            """
        ),
        {"acc": account_id},
    ).fetchone()
    if row is None:
        return 0
    last_dt = _parse_iso(row[0])
    if last_dt is None:
        return 0
    unlock = last_dt + timedelta(minutes=settings.cooldown_minutes)
    remaining = (unlock - _utcnow()).total_seconds()
    return max(0, int(remaining))


def submit_agent(
    session: Session,
    store: FileStore,
    settings: Settings,
    account_id: str,
    zip_bytes: bytes,
    ip_address: str,
) -> str:
    """Valide le zip, applique cooldown & taille de file, et place l'agent en file."""
    agent_name, sha = validate_agent_zip(zip_bytes, max_mb=settings.submission_max_mb)
    now_dt = _utcnow()
    now = _iso(now_dt)

    zip_key = f"agents/{sha}.zip"
    if not store.exists(zip_key):
        store.put_bytes(zip_key, zip_bytes)

    with write_tx(session):
        # Cooldown (vérifié dans la transaction)
        if settings.cooldown_minutes > 0:
            last = session.execute(
                text(
                    """
                    SELECT submitted_at FROM agents
                    WHERE account_id = :acc AND is_reference = 0
                    ORDER BY submitted_at DESC LIMIT 1
                    """
                ),
                {"acc": account_id},
            ).fetchone()
            if last is not None:
                last_dt = _parse_iso(last[0])
                if last_dt is not None:
                    diff = (now_dt - last_dt).total_seconds()
                    if diff < settings.cooldown_minutes * 60:
                        mins = int((settings.cooldown_minutes * 60 - diff + 59) // 60)
                        raise SubmissionRejectedError(
                            f"Cooldown actif : veuillez attendre encore {mins} minute(s) avant un nouvel envoi."
                        )

        # Un envoi qui arrive pendant que le précédent attend encore dans la file le remplace
        # et repart en fin de file.
        queued_prev = session.execute(
            text(
                "SELECT id FROM agents WHERE account_id = :acc AND status = 'queued'"
            ),
            {"acc": account_id},
        ).fetchall()
        for (old_id,) in queued_prev:
            session.execute(
                text(
                    """
                    UPDATE agents SET status = 'replaced', replaced_at = :now, updated_at = :now
                    WHERE id = :id
                    """
                ),
                {"id": old_id, "now": now},
            )

        # File pleine ?
        waiting_count = session.execute(
            text("SELECT COUNT(*) FROM agents WHERE status IN ('queued', 'validating', 'placing')")
        ).scalar_one()
        if waiting_count >= settings.queue_max:
            raise SubmissionRejectedError("File pleine : réessayez plus tard.")

        # Empêcher un double placement du même compte si déjà en validation/placement :
        # l'envoi reste en 'queued' et passera après, ou est refusé si déjà en cours ?
        # La spec dit : "Les envois ne sont jamais refusés parce qu'un autre est en cours : ils attendent leur tour dans une file".
        # On crée donc l'agent en 'queued'.
        agent_id = f"ag_{uuid.uuid4().hex[:16]}"
        session.execute(
            text(
                """
                INSERT INTO agents (
                    id, account_id, name, zip_key, sha256, status, is_active,
                    is_reference, is_anchor, elo, margin, provisional,
                    games_played, wins, draws, losses, placement_done, placement_total,
                    reject_reason, submitted_at, updated_at
                ) VALUES (
                    :id, :acc, :name, :zip_key, :sha, 'queued', 0,
                    0, 0, :elo, 999.0, 1,
                    0, 0, 0, 0, 0, :ptot,
                    '', :now, :now
                )
                """
            ),
            {
                "id": agent_id,
                "acc": account_id,
                "name": agent_name,
                "zip_key": zip_key,
                "sha": sha,
                "elo": settings.anchor_elo,
                "ptot": settings.placement_opponents,
                "now": now,
            },
        )

        session.execute(
            text(
                """
                INSERT INTO ip_events (ip_address, event_type, account_id, created_at)
                VALUES (:ip, 'submit', :acc, :now)
                """
            ),
            {"ip": ip_address, "acc": account_id, "now": now},
        )

        _promote_queued_agents_in_tx(session, settings, now)

    return agent_id


def _build_pair_games(
    left_agent_id: str,
    right_agent_id: str,
    num_maps: int,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    gen = rng or random.SystemRandom()
    games: list[dict[str, Any]] = []
    idx = 0
    for _ in range(num_maps):
        seed = gen.randrange(1, 2**31 - 1)
        games.append({
            "index": idx,
            "seed": seed,
            "left_agent_id": left_agent_id,
            "right_agent_id": right_agent_id,
        })
        idx += 1
        games.append({
            "index": idx,
            "seed": seed,
            "left_agent_id": right_agent_id,
            "right_agent_id": left_agent_id,
        })
        idx += 1
    return games


def _create_validation_job_in_tx(
    session: Session,
    settings: Settings,
    agent_id: str,
    now: str,
) -> str:
    dummy = session.execute(
        text("SELECT id FROM agents WHERE is_reference = 1 AND name = 'dummy_bot'")
    ).fetchone()
    if dummy is None:
        raise RuntimeError("Bot de référence dummy_bot introuvable en base.")
    dummy_id = dummy[0]

    # VALIDATION_GAMES parties contre dummy_bot, une de chaque côté
    num_games = max(2, settings.validation_games)
    games: list[dict[str, Any]] = []
    rng = random.SystemRandom()
    for i in range(num_games):
        seed = rng.randrange(1, 2**31 - 1)
        if i % 2 == 0:
            left_id, right_id = agent_id, dummy_id
        else:
            left_id, right_id = dummy_id, agent_id
        games.append({
            "index": i,
            "seed": seed,
            "left_agent_id": left_id,
            "right_agent_id": right_id,
        })

    job_id = f"job_{uuid.uuid4().hex[:16]}"
    payload = {"games": games}
    session.execute(
        text(
            """
            INSERT INTO jobs (
                id, type, priority, status, focus_agent_id, opponent_agent_id,
                payload_json, attempts, created_at
            ) VALUES (
                :id, 'validation', 1, 'pending', :focus, :opp,
                :payload, 0, :now
            )
            """
        ),
        {
            "id": job_id,
            "focus": agent_id,
            "opp": dummy_id,
            "payload": json.dumps(payload),
            "now": now,
        },
    )
    return job_id


def _promote_queued_agents_in_tx(session: Session, settings: Settings, now: str) -> None:
    in_progress = session.execute(
        text("SELECT COUNT(*) FROM agents WHERE status IN ('validating', 'placing')")
    ).scalar_one()

    while in_progress < settings.max_placements_in_progress:
        next_row = session.execute(
            text(
                """
                SELECT id, account_id FROM agents
                WHERE status = 'queued'
                  AND account_id NOT IN (
                      SELECT account_id FROM agents WHERE status IN ('validating', 'placing')
                  )
                ORDER BY submitted_at ASC
                LIMIT 1
                """
            )
        ).fetchone()
        if next_row is None:
            break
        agent_id = next_row[0]
        session.execute(
            text("UPDATE agents SET status = 'validating', updated_at = :now WHERE id = :id"),
            {"id": agent_id, "now": now},
        )
        _create_validation_job_in_tx(session, settings, agent_id, now)
        in_progress += 1


def _load_ranked_estimates(session: Session, settings: Settings) -> list[EloEstimate]:
    rows = session.execute(
        text(
            """
            SELECT id, elo, margin, provisional, games_played, wins, draws, losses
            FROM agents
            WHERE is_active = 1 AND status = 'ranked'
            ORDER BY elo DESC, id ASC
            """
        )
    ).fetchall()
    return [
        EloEstimate(
            agent_id=r[0],
            elo=float(r[1]),
            margin=float(r[2]),
            sigma=round(float(r[2]) / 1.96, 2),
            provisional=bool(r[3]),
            games_played=int(r[4]),
            wins=int(r[5]),
            draws=int(r[6]),
            losses=int(r[7]),
        )
        for r in rows
    ]


def _maybe_create_refinement_job_in_tx(
    session: Session,
    settings: Settings,
    now_dt: datetime,
) -> str | None:
    if not is_background_enabled(session, settings):
        return None

    # Respect de BACKGROUND_DUTY (50% par défaut)
    duty = max(1, min(100, int(settings.background_duty)))
    if duty < 100:
        last_ref = session.execute(
            text(
                """
                SELECT finished_at, duration_s FROM jobs
                WHERE type = 'refinement' AND status = 'finished'
                ORDER BY finished_at DESC LIMIT 1
                """
            )
        ).fetchone()
        if last_ref is not None and last_ref[0] and last_ref[1]:
            fin_dt = _parse_iso(last_ref[0])
            dur = float(last_ref[1])
            if fin_dt is not None and dur > 0:
                rest_s = dur * (100.0 - duty) / duty
                if (now_dt - fin_dt).total_seconds() < rest_s:
                    return None

    ranked = _load_ranked_estimates(session, settings)
    if len(ranked) < 2:
        return None

    anchor_row = session.execute(
        text("SELECT id FROM agents WHERE is_anchor = 1 AND is_active = 1 LIMIT 1")
    ).fetchone()
    anchor_id = anchor_row[0] if anchor_row else None

    pair_rows = session.execute(
        text(
            """
            SELECT left_agent_id, right_agent_id, COUNT(*)
            FROM matches
            WHERE is_active = 1
            GROUP BY left_agent_id, right_agent_id
            """
        )
    ).fetchall()
    pair_counts: dict[tuple[str, str], int] = {}
    for l_id, r_id, cnt in pair_rows:
        key = (l_id, r_id) if l_id < r_id else (r_id, l_id)
        pair_counts[key] = pair_counts.get(key, 0) + int(cnt)

    chosen = choose_refinement_pair(
        ranked,
        pair_counts,
        target_margin=settings.background_target_margin,
        max_games_per_pair=settings.max_games_per_pair,
        anchor_id=anchor_id,
    )
    if chosen is None:
        return None

    focus, opp = chosen
    games = _build_pair_games(focus.agent_id, opp.agent_id, max(1, settings.background_maps_per_job))
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    now = _iso(now_dt)
    session.execute(
        text(
            """
            INSERT INTO jobs (
                id, type, priority, status, focus_agent_id, opponent_agent_id,
                payload_json, attempts, created_at
            ) VALUES (
                :id, 'refinement', 3, 'pending', :focus, :opp,
                :payload, 0, :now
            )
            """
        ),
        {
            "id": job_id,
            "focus": focus.agent_id,
            "opp": opp.agent_id,
            "payload": json.dumps({"games": games}),
            "now": now,
        },
    )
    return job_id


def claim_job(session: Session, settings: Settings, worker_id: str) -> dict[str, Any] | None:
    """Réserve atomiquement le job le plus prioritaire, ou renvoie None si rien à faire."""
    now_dt = _utcnow()
    now = _iso(now_dt)

    with write_tx(session):
        # 1. Récupérer les baux expirés
        expired = session.execute(
            text(
                """
                SELECT id, type, focus_agent_id, attempts
                FROM jobs
                WHERE status = 'leased' AND lease_expires_at < :now
                """
            ),
            {"now": now},
        ).fetchall()

        for job_id, job_type, focus_id, attempts in expired:
            if int(attempts) >= 3:
                session.execute(
                    text(
                        """
                        UPDATE jobs
                        SET status = 'error',
                            lease_worker = NULL,
                            lease_expires_at = NULL,
                            finished_at = :now,
                            last_error = CASE WHEN last_error = '' THEN 'Bail expiré 3 fois'
                                              ELSE last_error || ' | Bail expiré 3 fois' END
                        WHERE id = :id
                        """
                    ),
                    {"id": job_id, "now": now},
                )
                if job_type in ("validation", "placement") and focus_id:
                    session.execute(
                        text(
                            """
                            UPDATE agents
                            SET status = 'rejected',
                                reject_reason = 'Échec technique répété du worker (3 tentatives).',
                                updated_at = :now
                            WHERE id = :aid AND status IN ('validating', 'placing')
                            """
                        ),
                        {"aid": focus_id, "now": now},
                    )
            else:
                session.execute(
                    text(
                        """
                        UPDATE jobs
                        SET status = 'pending', lease_worker = NULL, lease_expires_at = NULL
                        WHERE id = :id
                        """
                    ),
                    {"id": job_id},
                )

        # 2. Promouvoir la file si un créneau de placement est libre
        _promote_queued_agents_in_tx(session, settings, now)

        # 3. Prendre le job en attente le plus prioritaire (1=validation, 2=placement, 3=affinage)
        job_row = session.execute(
            text(
                """
                SELECT id, type, payload_json, attempts
                FROM jobs
                WHERE status = 'pending'
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """
            )
        ).fetchone()

        if job_row is None:
            new_ref_id = _maybe_create_refinement_job_in_tx(session, settings, now_dt)
            if new_ref_id is not None:
                job_row = session.execute(
                    text(
                        "SELECT id, type, payload_json, attempts FROM jobs WHERE id = :id"
                    ),
                    {"id": new_ref_id},
                ).fetchone()

        if job_row is None:
            return None

        job_id, job_type, payload_json, attempts = job_row
        lease_exp = _iso(now_dt + timedelta(seconds=settings.job_lease_seconds))
        session.execute(
            text(
                """
                UPDATE jobs
                SET status = 'leased',
                    attempts = :att,
                    lease_worker = :worker,
                    lease_expires_at = :exp,
                    started_at = COALESCE(started_at, :now)
                WHERE id = :id
                """
            ),
            {"att": int(attempts) + 1, "worker": worker_id, "exp": lease_exp, "now": now, "id": job_id},
        )

        payload = json.loads(payload_json)
        games = payload["games"]
        agent_ids = sorted({g["left_agent_id"] for g in games} | {g["right_agent_id"] for g in games})

        agents_info: dict[str, dict[str, str]] = {}
        for aid in agent_ids:
            arow = session.execute(
                text("SELECT id, name, sha256 FROM agents WHERE id = :id"),
                {"id": aid},
            ).fetchone()
            if arow is None:
                raise RuntimeError(f"Agent {aid} introuvable pour le job {job_id}")
            agents_info[aid] = {
                "id": arow[0],
                "name": arow[1],
                "sha256": arow[2],
                "zip_url": settings.url(f"/api/worker/agents/{arow[0]}/zip"),
            }

        return {
            "id": job_id,
            "type": job_type,
            "lease_expires_at": lease_exp,
            "agents": agents_info,
            "games": games,
            "game_version": settings.game_version,
            "limits": {
                "decision_mean_ms_limit": settings.decision_mean_ms_limit,
                "decision_timeout_ms": settings.decision_timeout_ms,
                "max_exceptions_per_game": settings.max_exceptions_per_game,
                "agent_load_timeout_s": settings.agent_load_timeout_s,
                "agent_memory_mb": settings.agent_memory_mb,
                "max_frames": settings.max_frames,
            },
        }


def heartbeat_job(session: Session, settings: Settings, job_id: str, worker_id: str) -> str:
    now_dt = _utcnow()
    lease_exp = _iso(now_dt + timedelta(seconds=settings.job_lease_seconds))
    with write_tx(session):
        res = session.execute(
            text(
                """
                UPDATE jobs
                SET lease_expires_at = :exp
                WHERE id = :id AND status = 'leased' AND lease_worker = :w
                """
            ),
            {"exp": lease_exp, "id": job_id, "w": worker_id},
        )
        if res.rowcount == 0:
            raise LookupError(f"Bail introuvable ou expiré pour le job {job_id}")
    return lease_exp


def fail_job(session: Session, settings: Settings, job_id: str, worker_id: str, reason: str) -> str:
    now = _iso(_utcnow())
    with write_tx(session):
        row = session.execute(
            text("SELECT type, focus_agent_id, attempts, status FROM jobs WHERE id = :id"),
            {"id": job_id},
        ).fetchone()
        if row is None:
            raise LookupError(f"Job inconnu : {job_id}")
        job_type, focus_id, attempts, status = row
        if status != "leased":
            return status

        if int(attempts) >= 3:
            new_status = "error"
            session.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = 'error', lease_worker = NULL, lease_expires_at = NULL,
                        finished_at = :now, last_error = :reason
                    WHERE id = :id
                    """
                ),
                {"id": job_id, "now": now, "reason": reason[:1500]},
            )
            if job_type in ("validation", "placement") and focus_id:
                session.execute(
                    text(
                        """
                        UPDATE agents
                        SET status = 'rejected',
                            reject_reason = :reason,
                            updated_at = :now
                        WHERE id = :aid AND status IN ('validating', 'placing')
                        """
                    ),
                    {"aid": focus_id, "now": now, "reason": f"Erreur technique worker : {reason[:500]}"},
                )
                _promote_queued_agents_in_tx(session, settings, now)
        else:
            new_status = "pending"
            session.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = 'pending', lease_worker = NULL, lease_expires_at = NULL,
                        last_error = :reason
                    WHERE id = :id
                    """
                ),
                {"id": job_id, "reason": reason[:1500]},
            )
    return new_status


def store_job_replay(store: FileStore, job_id: str, game_index: int, gzip_bytes: bytes) -> str:
    key = f"replays/jobs/{job_id}/{game_index}.json.gz"
    store.put_bytes(key, gzip_bytes)
    return key


def _recompute_all_elos_in_tx(session: Session, settings: Settings, now: str) -> None:
    agent_rows = session.execute(
        text(
            """
            SELECT id, is_anchor FROM agents
            WHERE is_active = 1 AND status = 'ranked'
            """
        )
    ).fetchall()
    if not agent_rows:
        return

    agent_ids = [r[0] for r in agent_rows]
    anchor_id = next((r[0] for r in agent_rows if r[1] == 1), None)
    if anchor_id is None:
        anchor_row = session.execute(
            text("SELECT id FROM agents WHERE is_reference = 1 AND name = :n"),
            {"n": settings.anchor_agent_name},
        ).fetchone()
        anchor_id = anchor_row[0] if anchor_row else agent_ids[0]

    match_rows = session.execute(
        text(
            """
            SELECT left_agent_id, right_agent_id, outcome
            FROM matches
            WHERE is_active = 1
            """
        )
    ).fetchall()
    records = [MatchRecord(left_id=r[0], right_id=r[1], outcome=r[2]) for r in match_rows]

    estimates = solve_bradley_terry(
        agent_ids,
        records,
        anchor_id=anchor_id,
        anchor_elo=settings.anchor_elo,
        prior_draws=2.0,
        provisional_margin=settings.provisional_margin,
    )

    for aid, est in estimates.items():
        session.execute(
            text(
                """
                UPDATE agents
                SET elo = :elo, margin = :margin, provisional = :prov,
                    games_played = :gp, wins = :w, draws = :d, losses = :l,
                    updated_at = :now
                WHERE id = :id
                """
            ),
            {
                "id": aid,
                "elo": est.elo,
                "margin": est.margin,
                "prov": 1 if est.provisional else 0,
                "gp": est.games_played,
                "w": est.wins,
                "d": est.draws,
                "l": est.losses,
                "now": now,
            },
        )
        session.execute(
            text(
                """
                INSERT INTO elo_history (agent_id, elo, margin, games_played, recorded_at)
                VALUES (:aid, :elo, :margin, :gp, :now)
                """
            ),
            {
                "aid": aid,
                "elo": est.elo,
                "margin": est.margin,
                "gp": est.games_played,
                "now": now,
            },
        )


def _prune_old_data_in_tx(
    session: Session,
    store: FileStore,
    settings: Settings,
    now_dt: datetime,
) -> None:
    # 1. Pour l'affinage, ne garder que REPLAYS_KEPT_PER_PAIR derniers replays par paire
    keep_n = max(0, int(settings.replays_kept_per_pair))
    ref_rows = session.execute(
        text(
            """
            SELECT id, left_agent_id, right_agent_id, replay_key, created_at
            FROM matches
            WHERE job_type = 'refinement' AND replay_deleted = 0 AND replay_key != ''
            ORDER BY created_at DESC, id DESC
            """
        )
    ).fetchall()
    seen_per_pair: dict[tuple[str, str], int] = {}
    for mid, l_id, r_id, rkey, _ in ref_rows:
        pair = (l_id, r_id) if l_id < r_id else (r_id, l_id)
        cnt = seen_per_pair.get(pair, 0) + 1
        seen_per_pair[pair] = cnt
        if cnt > keep_n:
            store.delete(rkey)
            session.execute(
                text("UPDATE matches SET replay_deleted = 1, replay_key = '' WHERE id = :id"),
                {"id": mid},
            )

    # 2. Replays d'agents remplacés supprimés après REPLAY_RETENTION_DAYS
    cutoff_replay = _iso(now_dt - timedelta(days=max(1, int(settings.replay_retention_days))))
    expired_replays = session.execute(
        text(
            """
            SELECT m.id, m.replay_key
            FROM matches m
            WHERE m.replay_deleted = 0 AND m.replay_key != ''
              AND m.left_agent_id IN (SELECT id FROM agents WHERE replaced_at IS NOT NULL AND replaced_at < :cut)
              AND m.right_agent_id IN (SELECT id FROM agents WHERE replaced_at IS NOT NULL AND replaced_at < :cut)
            """
        ),
        {"cut": cutoff_replay},
    ).fetchall()
    # Aussi si un seul des deux agents a été remplacé et l'autre n'est pas actif, ou simplement tout replay dont
    # un agent remplacé dépasse la rétention ? Spec : "ses replays sont supprimés après REPLAY_RETENTION_DAYS".
    expired_replays += session.execute(
        text(
            """
            SELECT m.id, m.replay_key
            FROM matches m
            WHERE m.replay_deleted = 0 AND m.replay_key != ''
              AND (
                  EXISTS (SELECT 1 FROM agents a WHERE a.id = m.left_agent_id AND a.replaced_at IS NOT NULL AND a.replaced_at < :cut)
                  OR
                  EXISTS (SELECT 1 FROM agents a WHERE a.id = m.right_agent_id AND a.replaced_at IS NOT NULL AND a.replaced_at < :cut)
              )
            """
        ),
        {"cut": cutoff_replay},
    ).fetchall()
    for mid, rkey in set(expired_replays):
        store.delete(rkey)
        session.execute(
            text("UPDATE matches SET replay_deleted = 1, replay_key = '' WHERE id = :id"),
            {"id": mid},
        )

    # 3. Purger les adresses IP de plus de 30 jours
    ip_cutoff = _iso(now_dt - timedelta(days=30))
    session.execute(
        text("DELETE FROM ip_events WHERE created_at < :cut"),
        {"cut": ip_cutoff},
    )


def complete_job(
    session: Session,
    store: FileStore,
    settings: Settings,
    job_id: str,
    worker_id: str,
    result_payload: dict[str, Any],
) -> dict[str, Any]:
    """Enregistre les résultats d'un job terminé, met à jour placement et Elo."""
    now_dt = _utcnow()
    now = _iso(now_dt)

    with write_tx(session):
        job_row = session.execute(
            text(
                """
                SELECT type, status, focus_agent_id, opponent_agent_id, payload_json, started_at, lease_worker
                FROM jobs WHERE id = :id
                """
            ),
            {"id": job_id},
        ).fetchone()
        if job_row is None:
            raise LookupError(f"Job inconnu : {job_id}")
        job_type, status, focus_id, opp_id, payload_json, started_at, lease_worker = job_row
        if status == "finished":
            return {"status": "already_finished"}
        if status != "leased":
            raise ValueError(f"Le job {job_id} n'est pas en cours (statut={status})")

        started_dt = _parse_iso(started_at) or now_dt
        duration_s = max(0.01, (now_dt - started_dt).total_seconds())

        session.execute(
            text(
                """
                UPDATE jobs
                SET status = 'finished', finished_at = :now, duration_s = :dur,
                    lease_worker = NULL, lease_expires_at = NULL
                WHERE id = :id
                """
            ),
            {"id": job_id, "now": now, "dur": duration_s},
        )

        games_spec = json.loads(payload_json)["games"]
        games_by_idx = {int(g["index"]): g for g in games_spec}
        reported_games = result_payload.get("games", [])

        # Validation
        if job_type == "validation":
            assert focus_id is not None
            validation_ok = bool(result_payload.get("validation_ok", False))
            reject_reason = str(result_payload.get("validation_error") or "")

            # Vérifier qu'aucune partie de validation n'a causé d'erreur fatale côté candidat
            for g in reported_games:
                idx = int(g["index"])
                spec_g = games_by_idx.get(idx)
                if spec_g is None:
                    continue
                cand_side = "left" if spec_g["left_agent_id"] == focus_id else "right"
                f_side = str(g.get("failure_side") or "")
                f_reason = str(g.get("failure_reason") or "")
                if f_side in (cand_side, "both"):
                    validation_ok = False
                    if not reject_reason:
                        reject_reason = f_reason or "Échec lors de la partie de validation."

            if not validation_ok:
                session.execute(
                    text(
                        """
                        UPDATE agents
                        SET status = 'rejected',
                            reject_reason = :reason,
                            updated_at = :now
                        WHERE id = :id
                        """
                    ),
                    {"id": focus_id, "reason": (reject_reason or "Agent refusé à la validation.")[:2000], "now": now},
                )
                _promote_queued_agents_in_tx(session, settings, now)
                return {"status": "rejected", "agent_id": focus_id}

            # Succès de validation -> passage au placement
            ranked = _load_ranked_estimates(session, settings)
            eligible_opps = [a for a in ranked if a.agent_id != focus_id]
            placement_total = min(settings.placement_opponents, len(eligible_opps))

            if placement_total == 0:
                # Aucun adversaire classé (cas dégénéré) : classement immédiat
                _activate_ranked_agent_in_tx(session, settings, focus_id, now)
                _recompute_all_elos_in_tx(session, settings, now)
                _promote_queued_agents_in_tx(session, settings, now)
                return {"status": "ranked", "agent_id": focus_id}

            session.execute(
                text(
                    """
                    UPDATE agents
                    SET status = 'placing',
                        validated_at = :now,
                        placement_done = 0,
                        placement_total = :ptot,
                        updated_at = :now
                    WHERE id = :id
                    """
                ),
                {"id": focus_id, "now": now, "ptot": placement_total},
            )
            first_opp = choose_next_placement_opponent(eligible_opps, set(), settings.anchor_elo, focus_id)
            assert first_opp is not None
            _create_placement_job_in_tx(session, settings, focus_id, first_opp.agent_id, now)
            return {"status": "placing", "agent_id": focus_id, "next_opponent": first_opp.agent_id}

        # Placement ou affinage : enregistrer toutes les parties jouées
        for g in reported_games:
            idx = int(g["index"])
            spec_g = games_by_idx[idx]
            match_id = f"m_{uuid.uuid4().hex[:16]}"
            temp_replay_key = f"replays/jobs/{job_id}/{idx}.json.gz"
            final_replay_key = ""
            if store.exists(temp_replay_key):
                final_replay_key = f"replays/{match_id}.json.gz"
                store.put_bytes(final_replay_key, store.get_bytes(temp_replay_key))
                store.delete(temp_replay_key)

            session.execute(
                text(
                    """
                    INSERT INTO matches (
                        id, job_id, job_type, seed, left_agent_id, right_agent_id,
                        outcome, end_frame, left_health, right_health,
                        left_mean_ms, left_max_ms, right_mean_ms, right_max_ms,
                        left_exceptions, right_exceptions, failure_side, failure_reason,
                        game_version, replay_key, replay_deleted, is_active, created_at
                    ) VALUES (
                        :id, :jid, :jtype, :seed, :lid, :rid,
                        :out, :ef, :lhp, :rhp,
                        :lms, :lmax, :rms, :rmax,
                        :lex, :rex, :fside, :freason,
                        :gver, :rkey, 0, 1, :now
                    )
                    """
                ),
                {
                    "id": match_id,
                    "jid": job_id,
                    "jtype": job_type,
                    "seed": int(spec_g["seed"]),
                    "lid": spec_g["left_agent_id"],
                    "rid": spec_g["right_agent_id"],
                    "out": str(g["outcome"]),
                    "ef": int(g["end_frame"]),
                    "lhp": float(g["left_health"]),
                    "rhp": float(g["right_health"]),
                    "lms": float(g.get("left_mean_ms", 0.0)),
                    "lmax": float(g.get("left_max_ms", 0.0)),
                    "rms": float(g.get("right_mean_ms", 0.0)),
                    "rmax": float(g.get("right_max_ms", 0.0)),
                    "lex": int(g.get("left_exceptions", 0)),
                    "rex": int(g.get("right_exceptions", 0)),
                    "fside": str(g.get("failure_side") or ""),
                    "freason": str(g.get("failure_reason") or "")[:500],
                    "gver": settings.game_version,
                    "rkey": final_replay_key,
                    "now": now,
                },
            )

        if job_type == "placement":
            assert focus_id is not None
            ag_row = session.execute(
                text("SELECT placement_done, placement_total FROM agents WHERE id = :id"),
                {"id": focus_id},
            ).fetchone()
            done = int(ag_row[0]) + 1
            total = int(ag_row[1])

            # Recalculer l'Elo provisoire du candidat avec les autres fixes
            ranked = _load_ranked_estimates(session, settings)
            fixed_elos = {a.agent_id: a.elo for a in ranked}
            cand_matches_rows = session.execute(
                text(
                    """
                    SELECT left_agent_id, right_agent_id, outcome
                    FROM matches
                    WHERE is_active = 1 AND (left_agent_id = :id OR right_agent_id = :id)
                    """
                ),
                {"id": focus_id},
            ).fetchall()
            cand_records = [MatchRecord(r[0], r[1], r[2]) for r in cand_matches_rows]
            played_opps = {
                (r[1] if r[0] == focus_id else r[0])
                for r in cand_matches_rows
            }
            anchor_row = session.execute(
                text("SELECT id FROM agents WHERE is_anchor = 1 AND is_active = 1 LIMIT 1")
            ).fetchone()
            anchor_id = anchor_row[0] if anchor_row else None

            interim_elo = estimate_single_agent_elo(
                focus_id,
                cand_records,
                fixed_elos,
                anchor_id=anchor_id,
                anchor_elo=settings.anchor_elo,
            )

            next_opp = None
            if done < total:
                next_opp = choose_next_placement_opponent(ranked, played_opps, interim_elo, focus_id)

            if next_opp is not None and done < total:
                session.execute(
                    text(
                        """
                        UPDATE agents
                        SET placement_done = :done, elo = :elo, updated_at = :now
                        WHERE id = :id
                        """
                    ),
                    {"id": focus_id, "done": done, "elo": interim_elo, "now": now},
                )
                _create_placement_job_in_tx(session, settings, focus_id, next_opp.agent_id, now)
                return {"status": "placing", "placement_done": done, "next_opponent": next_opp.agent_id}

            # Fin du placement -> remplacer l'ancien agent du compte et recalculer tout le classement
            session.execute(
                text("UPDATE agents SET placement_done = :done, updated_at = :now WHERE id = :id"),
                {"id": focus_id, "done": done, "now": now},
            )
            _activate_ranked_agent_in_tx(session, settings, focus_id, now)
            _recompute_all_elos_in_tx(session, settings, now)
            _prune_old_data_in_tx(session, store, settings, now_dt)
            _promote_queued_agents_in_tx(session, settings, now)
            return {"status": "ranked", "agent_id": focus_id}

        # Affinage terminé -> recalcul global + purge des replays excédentaires
        _recompute_all_elos_in_tx(session, settings, now)
        _prune_old_data_in_tx(session, store, settings, now_dt)
        return {"status": "refined"}


def _create_placement_job_in_tx(
    session: Session,
    settings: Settings,
    focus_id: str,
    opponent_id: str,
    now: str,
) -> str:
    games = _build_pair_games(focus_id, opponent_id, max(1, settings.placement_maps_per_opponent))
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    session.execute(
        text(
            """
            INSERT INTO jobs (
                id, type, priority, status, focus_agent_id, opponent_agent_id,
                payload_json, attempts, created_at
            ) VALUES (
                :id, 'placement', 2, 'pending', :focus, :opp,
                :payload, 0, :now
            )
            """
        ),
        {
            "id": job_id,
            "focus": focus_id,
            "opp": opponent_id,
            "payload": json.dumps({"games": games}),
            "now": now,
        },
    )
    return job_id


def _activate_ranked_agent_in_tx(
    session: Session,
    settings: Settings,
    new_agent_id: str,
    now: str,
) -> None:
    acc_row = session.execute(
        text("SELECT account_id FROM agents WHERE id = :id"),
        {"id": new_agent_id},
    ).fetchone()
    if acc_row is None:
        return
    account_id = acc_row[0]

    old_rows = session.execute(
        text(
            """
            SELECT id FROM agents
            WHERE account_id = :acc AND id != :new_id AND is_active = 1
            """
        ),
        {"acc": account_id, "new_id": new_agent_id},
    ).fetchall()
    for (old_id,) in old_rows:
        session.execute(
            text(
                """
                UPDATE agents
                SET is_active = 0, status = 'replaced', replaced_at = :now, updated_at = :now
                WHERE id = :id
                """
            ),
            {"id": old_id, "now": now},
        )
        # Ses résultats restent en base, marqués inactifs
        session.execute(
            text(
                """
                UPDATE matches
                SET is_active = 0
                WHERE left_agent_id = :id OR right_agent_id = :id
                """
            ),
            {"id": old_id},
        )

    session.execute(
        text(
            """
            UPDATE agents
            SET status = 'ranked', is_active = 1, ranked_at = :now, updated_at = :now
            WHERE id = :id
            """
        ),
        {"id": new_agent_id, "now": now},
    )


def delete_account(
    session: Session,
    store: FileStore,
    settings: Settings,
    account_id: str,
) -> None:
    """Supprime le compte, ses agents, ses zips, ses replays et recalcule le classement."""
    now = _iso(_utcnow())
    with write_tx(session):
        agent_rows = session.execute(
            text("SELECT id, zip_key FROM agents WHERE account_id = :acc"),
            {"acc": account_id},
        ).fetchall()
        agent_ids = [r[0] for r in agent_rows]
        zip_keys = [r[1] for r in agent_rows]

        for aid in agent_ids:
            replay_rows = session.execute(
                text(
                    "SELECT id, replay_key FROM matches WHERE left_agent_id = :id OR right_agent_id = :id"
                ),
                {"id": aid},
            ).fetchall()
            for _, rkey in replay_rows:
                if rkey:
                    store.delete(rkey)
            session.execute(
                text("DELETE FROM matches WHERE left_agent_id = :id OR right_agent_id = :id"),
                {"id": aid},
            )
            session.execute(
                text("DELETE FROM jobs WHERE focus_agent_id = :id OR opponent_agent_id = :id"),
                {"id": aid},
            )

        session.execute(
            text("DELETE FROM ip_events WHERE account_id = :acc AND event_type != 'account_create'"),
            {"acc": account_id},
        )
        session.execute(
            text("UPDATE ip_events SET account_id = NULL WHERE account_id = :acc"),
            {"acc": account_id},
        )
        session.execute(text("DELETE FROM accounts WHERE id = :acc"), {"acc": account_id})

        # Supprimer les zips qui ne sont plus référencés par aucun autre agent
        for zkey in set(zip_keys):
            ref_cnt = session.execute(
                text("SELECT COUNT(*) FROM agents WHERE zip_key = :zk"),
                {"zk": zkey},
            ).scalar_one()
            if ref_cnt == 0:
                store.delete(zkey)

        _recompute_all_elos_in_tx(session, settings, now)
        _promote_queued_agents_in_tx(session, settings, now)


def disable_agent(session: Session, settings: Settings, agent_id: str) -> None:
    """Action admin : désactive un agent et recalcule le classement."""
    now = _iso(_utcnow())
    with write_tx(session):
        session.execute(
            text(
                """
                UPDATE agents
                SET is_active = 0, status = 'disabled', replaced_at = :now, updated_at = :now
                WHERE id = :id AND is_reference = 0
                """
            ),
            {"id": agent_id, "now": now},
        )
        session.execute(
            text("UPDATE matches SET is_active = 0 WHERE left_agent_id = :id OR right_agent_id = :id"),
            {"id": agent_id},
        )
        session.execute(
            text(
                """
                UPDATE jobs SET status = 'error', last_error = 'Désactivé par un administrateur', finished_at = :now
                WHERE (focus_agent_id = :id OR opponent_agent_id = :id) AND status IN ('pending', 'leased')
                """
            ),
            {"id": agent_id, "now": now},
        )
        _recompute_all_elos_in_tx(session, settings, now)
        _promote_queued_agents_in_tx(session, settings, now)


def clear_queue(session: Session) -> int:
    """Action admin : vide la file d'attente (agents en attente + jobs pending)."""
    now = _iso(_utcnow())
    with write_tx(session):
        res1 = session.execute(
            text(
                """
                UPDATE agents
                SET status = 'rejected', reject_reason = 'File vidée par un administrateur.', updated_at = :now
                WHERE status = 'queued'
                """
            ),
            {"now": now},
        )
        res2 = session.execute(
            text("DELETE FROM jobs WHERE status = 'pending'")
        )
        return int((res1.rowcount or 0) + (res2.rowcount or 0))


def retry_error_job(session: Session, settings: Settings, job_id: str) -> bool:
    """Action admin : remet en file un job en erreur."""
    now = _iso(_utcnow())
    with write_tx(session):
        row = session.execute(
            text("SELECT type, focus_agent_id FROM jobs WHERE id = :id AND status = 'error'"),
            {"id": job_id},
        ).fetchone()
        if row is None:
            return False
        jtype, focus_id = row
        session.execute(
            text(
                """
                UPDATE jobs
                SET status = 'pending', attempts = 0, lease_worker = NULL,
                    lease_expires_at = NULL, finished_at = NULL, last_error = ''
                WHERE id = :id
                """
            ),
            {"id": job_id},
        )
        if focus_id and jtype in ("validation", "placement"):
            new_st = "validating" if jtype == "validation" else "placing"
            session.execute(
                text("UPDATE agents SET status = :st, reject_reason = '', updated_at = :now WHERE id = :id"),
                {"st": new_st, "now": now, "id": focus_id},
            )
    return True


def estimate_queue_wait(session: Session, agent_id: str) -> tuple[int | None, float | None]:
    """Retourne (position_dans_la_file, estimation_en_secondes) pour un agent en attente."""
    queued_rows = session.execute(
        text(
            """
            SELECT id FROM agents
            WHERE status IN ('queued', 'validating', 'placing')
            ORDER BY
                CASE status WHEN 'placing' THEN 0 WHEN 'validating' THEN 1 ELSE 2 END,
                submitted_at ASC
            """
        )
    ).fetchall()
    ids = [r[0] for r in queued_rows]
    if agent_id not in ids:
        return None, None
    pos = ids.index(agent_id) + 1

    # Durée moyenne des derniers placements (somme des jobs de placement par agent sur les 10 derniers)
    avg_dur_rows = session.execute(
        text(
            """
            SELECT SUM(duration_s) FROM jobs
            WHERE type IN ('validation', 'placement') AND status = 'finished' AND duration_s IS NOT NULL
            GROUP BY focus_agent_id
            ORDER BY MAX(finished_at) DESC
            LIMIT 10
            """
        )
    ).fetchall()
    if avg_dur_rows:
        avg_placement_s = sum(float(r[0]) for r in avg_dur_rows) / len(avg_dur_rows)
    else:
        avg_placement_s = 180.0  # estimation par défaut (~3 min en V0)
    return pos, round(pos * avg_placement_s, 1)
