"""Computes daily reports and alerts for the mission stored in ``data/astro.db``.

Run it after ``generar_datos`` (or whenever new measurements arrive). It is
idempotent: running it again updates the same reports instead of duplicating them.

Run from the repo root::

    python -m scripts.procesar_mision              # every astronaut, every day
    python -m scripts.procesar_mision --log 2      # one mission log
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from services.database import sesion
from services.reportes import process_mission_log


def main() -> None:
    parser = argparse.ArgumentParser(description="Calcula reportes diarios y alertas.")
    parser.add_argument("--log", type=int, help="ID del mission log (por defecto, todos los activos).")
    parser.add_argument("--db", type=Path, help="Base a procesar (por defecto, data/astro.db).")
    args = parser.parse_args()

    with sesion(args.db) as db:
        logs = [db.mission_logs.obtener(args.log)] if args.log else db.mission_logs.activos()
        names = {u["id"]: u["full_name"] for u in db.usuarios.listar()}

    for log in logs:
        if log is None:
            raise SystemExit(f"mission_log {args.log} no existe.")
        results = process_mission_log(log["id"], db_path=args.db)
        processed = [r for r in results if r.processed]
        triage = Counter(r.evaluation.triage_status for r in processed)
        alerts = Counter(draft.severity for r in processed for _, draft in r.alerts)
        avg = sum(r.readiness_score for r in processed) / len(processed) if processed else 0.0
        print(
            f"[OK] {names[log['id_user']]:<16} {len(processed):>3} días | "
            f"verde {triage['green']:>2} · amarillo {triage['yellow']:>2} · rojo {triage['red']:>2} | "
            f"alertas: {alerts['red']} rojas, {alerts['yellow']} amarillas | puntaje medio {avg:.1f}"
        )


if __name__ == "__main__":
    main()
