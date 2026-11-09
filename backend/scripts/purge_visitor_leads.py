"""Export, then erase, the visitor leads stored in the ``visitors`` table.

Background (SEC-05): the website lead-capture endpoint was removed because
nothing in the frontend used it, but rows it collected earlier (e-mail, name,
company, role, notes, IP address, user agent) remain in the database. This
script lets the owner keep an export and then delete them. It never drops the
table, so it can be run safely against SQLite or the Databricks warehouse.

Usage (from the repository root, with the backend environment configured):

    # 1. Count only (default, changes nothing)
    python backend/scripts/purge_visitor_leads.py

    # 2. Export to a CSV you keep somewhere access-controlled
    python backend/scripts/purge_visitor_leads.py --export leads.csv

    # 3. Delete every row (asks for no confirmation beyond the flag)
    python backend/scripts/purge_visitor_leads.py --export leads.csv --delete --yes

Deleting is irreversible. Export first if the owner wants to keep the data.
Once the table is empty and the admin view in Settings is no longer wanted,
the remaining list/export/delete endpoints, the ``Visitor`` model and the
table itself can be removed in a later change.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy import delete, func, select  # noqa: E402

from app.api.visitors import _csv_cell  # noqa: E402
from app.database import get_db_context  # noqa: E402
from app.models.visitor import Visitor  # noqa: E402

COLUMNS = ("id", "email", "full_name", "company", "role", "notes", "source",
           "ip_address", "user_agent", "created_at", "updated_at")


async def run(export: str | None, do_delete: bool) -> int:
    async with get_db_context() as db:
        total = (await db.execute(select(func.count()).select_from(Visitor))).scalar_one()
        print(f"visitor leads stored: {total}")
        if export:
            rows = (await db.execute(select(Visitor).order_by(Visitor.created_at))).scalars().all()
            with open(export, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(COLUMNS)
                for row in rows:
                    writer.writerow([_csv_cell("" if getattr(row, c) is None else str(getattr(row, c))) for c in COLUMNS])
            print(f"exported {len(rows)} rows to {export}")
        if do_delete:
            await db.execute(delete(Visitor))
            print(f"deleted {total} rows (table kept)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--export", help="write every lead to this CSV file first")
    parser.add_argument("--delete", action="store_true", help="delete every visitor lead row")
    parser.add_argument("--yes", action="store_true", help="required together with --delete")
    args = parser.parse_args(argv)
    if args.delete and not args.yes:
        parser.error("--delete is irreversible; add --yes to confirm")
    return asyncio.run(run(args.export, args.delete))


if __name__ == "__main__":
    raise SystemExit(main())
