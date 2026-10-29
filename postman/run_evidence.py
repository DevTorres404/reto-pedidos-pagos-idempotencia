"""Run `evidence.sql` against the two databases and print the result.

Usable without the `sqlite3` CLI, which is not installed everywhere:

    .\\.venv\\Scripts\\python.exe postman\\run_evidence.py

The connection string in the script is relative to the repository root, so run it
from there. Stop the services first if you want the files to be quiet, though SQLite
reads a WAL database fine while it is open.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ORDER_DB = ROOT / "order_api" / "orders.db"
DEFAULT_PAYMENT_DB = ROOT / "payment_mock" / "payments.db"
SCRIPT = Path(__file__).with_name("evidence.sql")


def _statements(sql: str) -> list[str]:
    """Split on statement boundaries. Good enough for this file: no string literals
    contain a semicolon."""
    chunks = []
    buffer: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            chunks.append("\n".join(buffer))
            buffer = []
    if buffer:
        chunks.append("\n".join(buffer))
    return chunks


def _print_rows(cursor: sqlite3.Cursor) -> None:
    rows = cursor.fetchall()
    if not rows:
        print("  (0 filas)")
        return
    headers = [column[0] for column in cursor.description or []]
    widths = [len(header) for header in headers]
    cells = []
    for row in rows:
        rendered = ["NULL" if value is None else str(value) for value in row]
        cells.append(rendered)
        widths = [max(width, len(value)) for width, value in zip(widths, rendered)]

    print("  " + "  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print("  " + "  ".join("-" * width for width in widths))
    for rendered in cells:
        print("  " + "  ".join(value.ljust(width) for value, width in zip(rendered, widths)))


def main(argv: list[str]) -> int:
    order_db = Path(argv[1]) if len(argv) > 1 else DEFAULT_ORDER_DB
    payment_db = Path(argv[2]) if len(argv) > 2 else DEFAULT_PAYMENT_DB

    if not order_db.exists():
        print(f"Missing {order_db}. Run the demo first.", file=sys.stderr)
        return 1
    if not payment_db.exists():
        print(f"Missing {payment_db}. Run the demo first.", file=sys.stderr)
        return 1

    sql = SCRIPT.read_text(encoding="utf-8").replace(
        "payment_mock/payments.db", str(payment_db).replace("\\", "/")
    )

    conn = sqlite3.connect(str(order_db))
    conn.row_factory = sqlite3.Row
    try:
        for index, statement in enumerate(_statements(sql), start=1):
            print(f"\n[{index}] {' '.join(statement.split())[:110]}")
            cursor = conn.execute(statement)
            _print_rows(cursor)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
