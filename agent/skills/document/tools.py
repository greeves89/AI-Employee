"""Document Analysis skill — tools.py

Extracts text from PDF, DOCX, TXT, MD, and other text formats.
"""

from __future__ import annotations

from pathlib import Path

from app.skills_loader import skill_tool


@skill_tool(
    name="analyze_document",
    description=(
        "Extract and analyse text from documents (PDF, DOCX, TXT, MD). "
        "Returns the full extracted text plus metadata (page count, word count). "
        "Use this to read contracts, reports, or any document before asking the LLM "
        "to summarise, review, or answer questions about it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or workspace-relative path to the document",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default: 50000). Use 0 for unlimited.",
                "default": 50000,
            },
        },
        "required": ["path"],
    },
)
async def analyze_document(params: dict) -> str:
    """Extract text from PDF, DOCX, or plain-text documents."""
    raw_path = params["path"]
    path = raw_path if Path(raw_path).is_absolute() else f"/workspace/{raw_path.lstrip('/')}"
    max_chars = int(params.get("max_chars", 50000))
    ext = Path(path).suffix.lower()

    if not Path(path).exists():
        return f"Error: File not found: {path}"

    text = ""
    meta: dict = {}

    try:
        if ext == ".pdf":
            try:
                import fitz  # PyMuPDF  # noqa: PLC0415
                doc = fitz.open(path)
                meta["pages"] = doc.page_count
                text = "\n\n".join(page.get_text() for page in doc)
            except ImportError:
                from pdfminer.high_level import extract_text as _pdf_extract  # noqa: PLC0415
                text = _pdf_extract(path)

        elif ext in (".docx", ".doc"):
            try:
                import docx  # python-docx  # noqa: PLC0415
                document = docx.Document(path)
                text = "\n".join(p.text for p in document.paragraphs)
            except ImportError:
                return "Error: python-docx not installed. Run `pip install python-docx`."

        elif ext in (".txt", ".md", ".rst", ".csv", ".log"):
            text = Path(path).read_text(encoding="utf-8", errors="replace")

        else:
            return (
                f"Error: Unsupported format '{ext}'. "
                "Supported: pdf, docx, txt, md, rst, csv, log."
            )

    except Exception as exc:
        return f"Error extracting text: {exc}"

    word_count = len(text.split())
    char_count = len(text)
    meta.update({"word_count": word_count, "char_count": char_count})

    header_lines = [
        f"## Document: {Path(path).name}",
        f"- **Words:** {word_count:,}",
        f"- **Characters:** {char_count:,}",
    ]
    if "pages" in meta:
        header_lines.append(f"- **Pages:** {meta['pages']}")
    header_lines += ["", "---", ""]

    header = "\n".join(header_lines)
    full = header + text

    if max_chars and len(full) > max_chars:
        full = full[:max_chars] + f"\n\n... (truncated — {char_count:,} chars total)"
    return full
