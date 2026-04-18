"""Finance Reports skill — tools.py

Generates structured Markdown finance/budget reports from CSV or Excel files.
"""

from __future__ import annotations

from pathlib import Path

from app.skills_loader import skill_tool


@skill_tool(
    name="generate_finance_report",
    description=(
        "Generate a structured finance / budget report from a CSV or Excel file. "
        "Detects amount columns automatically, groups by category, computes totals, "
        "finds top-10 largest expenses, and returns a Markdown report. "
        "Ideal for expense lists, bank exports, or any tabular cost data."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the CSV or Excel file with financial data",
            },
            "amount_column": {
                "type": "string",
                "description": "Name of the column containing amounts (auto-detected if omitted)",
            },
            "category_column": {
                "type": "string",
                "description": "Name of the column to group by (auto-detected if omitted)",
            },
            "currency": {
                "type": "string",
                "description": "Currency symbol to use in the report (default: €)",
                "default": "€",
            },
        },
        "required": ["path"],
    },
)
async def generate_finance_report(params: dict) -> str:
    """Generate a Markdown finance report from a CSV/Excel file."""
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError:
        return "Error: pandas is not installed. Run `pip install pandas openpyxl` first."

    raw_path = params["path"]
    path = raw_path if Path(raw_path).is_absolute() else f"/workspace/{raw_path.lstrip('/')}"
    currency = params.get("currency", "€")
    amount_col: str | None = params.get("amount_column")
    category_col: str | None = params.get("category_column")

    try:
        ext = Path(path).suffix.lower()
        df = pd.read_csv(path) if ext == ".csv" else pd.read_excel(path)
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as exc:
        return f"Error reading file: {exc}"

    # Auto-detect amount column
    if not amount_col:
        num_cols = df.select_dtypes(include="number").columns.tolist()
        candidates = [
            c for c in num_cols
            if any(k in c.lower() for k in ("amount", "betrag", "wert", "summe", "cost", "price", "total", "kosten"))
        ]
        amount_col = candidates[0] if candidates else (num_cols[0] if num_cols else None)
    if not amount_col or amount_col not in df.columns:
        return (
            f"Error: Could not find an amount column. "
            f"Available columns: {', '.join(df.columns)}. Specify 'amount_column'."
        )

    # Auto-detect category column
    if not category_col:
        str_cols = df.select_dtypes(include="object").columns.tolist()
        candidates = [
            c for c in str_cols
            if any(k in c.lower() for k in ("category", "kategorie", "type", "typ", "art", "group", "konto", "bereich"))
        ]
        category_col = candidates[0] if candidates else (str_cols[0] if str_cols else None)

    total = df[amount_col].sum()
    lines = [
        f"# Finance Report: {Path(path).name}",
        f"**Total:** {currency} {total:,.2f}  |  **Rows:** {len(df):,}",
        "",
    ]

    if category_col and category_col in df.columns:
        grouped = (
            df.groupby(category_col)[amount_col]
            .agg(["sum", "count"])
            .sort_values("sum", ascending=False)
            .rename(columns={"sum": "Total", "count": "Entries"})
        )
        lines += ["## By Category", ""]
        lines.append(f"{'Category':<30} {'Total':>14} {'Entries':>8} {'Share':>7}")
        lines.append("-" * 62)
        for cat, row in grouped.iterrows():
            share = row["Total"] / total * 100 if total else 0
            lines.append(
                f"{str(cat):<30} {currency} {row['Total']:>11,.2f} "
                f"{int(row['Entries']):>8} {share:>6.1f}%"
            )
        lines.append("")

    top = df.nlargest(10, amount_col)
    lines += ["## Top 10 Largest Items", ""]
    for _, row in top.iterrows():
        desc = ""
        for col in df.columns:
            if col != amount_col and df[col].dtype == object:
                desc = str(row[col])[:40]
                break
        lines.append(f"- {currency} {row[amount_col]:,.2f}  — {desc}")

    return "\n".join(lines)
