"""Summarize Milo's per-session usage CSV: minutes, dollars, $/minute and $/session.

Run from server/: ``uv run python tools/usage_report.py [path/to/usage.csv]``
(default: ``$PIP_USAGE_FILE`` or ``~/.config/little-explorer/usage.csv``). Costs are the agent's
estimates from list prices; the OpenAI usage dashboard is the source of truth.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from little_explorer_agent.usage import usage_file  # noqa: E402


def _float(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def summarize(rows: list[dict[str, str]]) -> dict[str, dict[str, dict[str, float]]]:
    """Totals overall, per day (local date of the session start) and per model."""
    groups: dict[str, dict[str, dict[str, float]]] = {"total": {}, "day": {}, "model": {}}
    buckets: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"sessions": 0, "minutes": 0.0, "usd": 0.0})
    for row in rows:
        minutes = _float(row.get("duration_s")) / 60
        usd = _float(row.get("est_usd"))
        for group, key in (("total", "all"), ("day", (row.get("started_at") or "")[:10]), ("model", row.get("model") or "?")):
            b = buckets[(group, key)]
            b["sessions"] += 1
            b["minutes"] += minutes
            b["usd"] += usd
    for (group, key), b in buckets.items():
        b["usd_per_min"] = b["usd"] / b["minutes"] if b["minutes"] else 0.0
        b["usd_per_session"] = b["usd"] / b["sessions"] if b["sessions"] else 0.0
        groups[group][key] = b
    return groups


def _table(title: str, data: dict[str, dict[str, float]]) -> str:
    lines = [title, f"  {'':<22}{'sessions':>9}{'minutes':>10}{'$':>10}{'$/min':>9}{'$/session':>11}"]
    for key in sorted(data):
        b = data[key]
        lines.append(
            f"  {key:<22}{int(b['sessions']):>9}{b['minutes']:>10.1f}{b['usd']:>10.2f}"
            f"{b['usd_per_min']:>9.3f}{b['usd_per_session']:>11.3f}"
        )
    return "\n".join(lines)


def report(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "No sessions logged yet."
    s = summarize(rows)
    return "\n\n".join([_table("Total", s["total"]), _table("Per day", s["day"]), _table("Per model", s["model"])])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", type=Path, help="usage CSV (default: %(default)s)", default=None)
    args = parser.parse_args(argv)
    path = args.path or usage_file()
    if not path.is_file():
        print(f"No usage file at {path} yet.")
        return 0
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"{path}\n")
    print(report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
