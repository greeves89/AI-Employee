"""Spreadsheet Analysis skill — tools.py

Analyses CSV, XLSX, XLS, ODS files using pandas.
"""

from __future__ import annotations

from pathlib import Path

from app.skills_loader import skill_tool

MAX_OUTPUT_CHARS = 30_000


@skill_tool(
    name="analyze_spreadsheet",
    description=(
        "Read and analyze spreadsheet or CSV files (xlsx, xls, csv, ods). "
        "Returns a structured summary with row/column counts, data types, "
        "basic statistics, and the first rows as a preview. "
        "Optionally filter to a specific sheet."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or workspace-relative path to the spreadsheet file",
            },
            "sheet": {
                "type": "string",
                "description": "Sheet name to analyse (default: first sheet)",
            },
            "preview_rows": {
                "type": "integer",
                "description": "Number of preview rows to include (default: 10)",
                "default": 10,
            },
        },
        "required": ["path"],
    },
)
async def analyze_spreadsheet(params: dict) -> str:
    """Read and analyse a spreadsheet/CSV file using pandas."""
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError:
        return "Error: pandas is not installed. Run `pip install pandas openpyxl` first."

    raw_path = params["path"]
    path = raw_path if Path(raw_path).is_absolute() else f"/workspace/{raw_path.lstrip('/')}"
    sheet = params.get("sheet")
    preview_rows = int(params.get("preview_rows", 10))

    try:
        ext = Path(path).suffix.lower()
        if ext == ".csv":
            df = pd.read_csv(path)
        elif ext in (".xls", ".xlsx", ".xlsm", ".xlsb"):
            df = pd.read_excel(path, sheet_name=sheet or 0)
        elif ext == ".ods":
            df = pd.read_excel(path, sheet_name=sheet or 0, engine="odf")
        else:
            return f"Error: Unsupported file type '{ext}'. Supported: csv, xlsx, xls, ods."
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as exc:
        return f"Error reading file: {exc}"

    lines: list[str] = [
        f"## Spreadsheet Analysis: {Path(path).name}",
        f"- **Rows:** {len(df):,}",
        f"- **Columns:** {len(df.columns)}",
        f"- **Column names:** {', '.join(str(c) for c in df.columns)}",
        "",
        "### Data Types",
    ]
    for col, dtype in df.dtypes.items():
        null_count = int(df[col].isna().sum())
        lines.append(f"- `{col}`: {dtype} ({null_count} nulls)")

    num_cols = df.select_dtypes(include="number")
    if not num_cols.empty:
        lines += ["", "### Numeric Statistics"]
        desc = num_cols.describe().round(2)
        lines.append(desc.to_string())

    lines += ["", f"### Preview (first {preview_rows} rows)"]
    lines.append(df.head(preview_rows).to_string(index=False))

    result = "\n".join(lines)
    if len(result) > MAX_OUTPUT_CHARS:
        result = result[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
    return result
