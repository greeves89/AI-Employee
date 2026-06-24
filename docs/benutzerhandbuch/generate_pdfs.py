#!/usr/bin/env python3
"""
Generate branded PDFs from Markdown documentation files.
Uses mindsquare corporate colors:
  - Primary: Gray #4a4a4a
  - Accent: Orange #e8960c / Amber #fdc300

Professional cover page + enterprise document styling.
"""

import markdown
from weasyprint import HTML
import os
import re
import sys

# mindsquare brand colors (Gray + Orange, NOT blue!)
PRIMARY = "#4a4a4a"           # mindsquare Gray (headings, logo text)
PRIMARY_DARK = "#333333"      # Dark gray for emphasis
PRIMARY_LIGHT = "#f0f0f0"    # Light gray background
ACCENT = "#e8960c"            # mindsquare Orange (accent lines, highlights)
ACCENT_DARK = "#d4820a"       # Darker orange
ACCENT_LIGHT = "#fef6e8"     # Very light orange bg
LOGO_RED = "#c0392b"          # Second square in logo (red-orange)
TEXT_COLOR = "#2d2d2d"        # Dark gray for body text
TEXT_SECONDARY = "#5a5a5a"    # Medium gray
TEXT_MUTED = "#999999"        # Light gray for meta text
BG_LIGHT = "#f8f8f8"         # Very light background
WHITE = "#ffffff"
BORDER = "#e0e0e0"           # Gray border
BORDER_LIGHT = "#eeeeee"     # Light gray border

# Document metadata mapping
DOC_META = {
    "README.md": {
        "subtitle": "Klick-für-Klick-Anleitung aller Funktionen der Plattform",
        "doc_type": "Benutzerhandbuch",
    },
    "hoermann-technische-dokumentation.md": {
        "subtitle": "Enterprise-Plattform f\u00fcr KI-gest\u00fctzte Kommunikation",
        "doc_type": "Technische Dokumentation",
        "icon": "cog",
    },
    "hoermann-datenschutzdokumentation.md": {
        "subtitle": "DSGVO-Compliance & Datenschutz-Folgenabsch\u00e4tzung",
        "doc_type": "Datenschutzdokumentation",
        "icon": "shield",
    },
    "hoermann-eu-ai-act-bewertung.md": {
        "subtitle": "Risikobewertung gem\u00e4\u00df Verordnung (EU) 2024/1689",
        "doc_type": "EU AI Act Bewertung",
        "icon": "scale",
    },
    "hoermann-avv-template.md": {
        "subtitle": "Vertrag gem\u00e4\u00df Art. 28 DS-GVO f\u00fcr Self-Hosted + Support",
        "doc_type": "Auftragsverarbeitungsvertrag",
        "icon": "handshake",
    },
    "voiceai-pro-features.md": {
        "subtitle": "Enterprise KI-Kommunikationsplattform",
        "doc_type": "Feature \u00dcbersicht",
        "icon": "star",
    },
}

CSS_TEMPLATE = f"""
@import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

/* ===== PAGE SETUP ===== */
@page {{
    size: A4;
    margin: 22mm 18mm 25mm 18mm;

    @bottom-center {{
        content: counter(page);
        font-family: 'Open Sans', 'Helvetica Neue', Arial, sans-serif;
        font-size: 9pt;
        color: {TEXT_MUTED};
        padding-top: 8px;
        border-top: 0.5pt solid {BORDER};
    }}
    @bottom-left {{
        content: "AI Employee · Benutzerhandbuch";
        font-family: 'Open Sans', 'Helvetica Neue', Arial, sans-serif;
        font-size: 7.5pt;
        color: {TEXT_MUTED};
        padding-top: 8px;
        border-top: 0.5pt solid {BORDER};
    }}
    @bottom-right {{
        content: "Vertraulich";
        font-family: 'Open Sans', 'Helvetica Neue', Arial, sans-serif;
        font-size: 7.5pt;
        color: {PRIMARY};
        font-weight: 600;
        padding-top: 8px;
        border-top: 0.5pt solid {BORDER};
    }}
}}

@page :first {{
    margin: 0;
    @bottom-left {{ content: ""; border: none; }}
    @bottom-right {{ content: ""; border: none; }}
    @bottom-center {{ content: ""; border: none; }}
}}

/* ===== BODY ===== */
body {{
    font-family: 'Open Sans', 'Helvetica Neue', Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.65;
    color: {TEXT_COLOR};
    font-weight: 400;
}}

/* ===== COVER PAGE ===== */
.cover-page {{
    width: 210mm;
    height: 297mm;
    position: relative;
    overflow: hidden;
    page-break-after: always;
}}

.cover-top-bar {{
    background: {WHITE};
    height: 35mm;
    width: 100%;
    position: relative;
    border-bottom: none;
}}

.cover-accent-line {{
    background: {ACCENT};
    height: 3mm;
    width: 100%;
}}

.cover-body {{
    padding: 25mm 22mm 0 22mm;
}}

.cover-doc-type {{
    font-size: 10pt;
    font-weight: 600;
    color: {ACCENT};
    text-transform: uppercase;
    letter-spacing: 3px;
    margin-bottom: 12px;
}}

.cover-title {{
    font-size: 32pt;
    font-weight: 800;
    color: {TEXT_COLOR};
    line-height: 1.15;
    margin-bottom: 10px;
    letter-spacing: -0.8px;
}}

.cover-subtitle {{
    font-size: 13pt;
    font-weight: 400;
    color: {TEXT_SECONDARY};
    line-height: 1.5;
    margin-bottom: 40px;
}}

.cover-meta-table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 10px;
}}

.cover-meta-table td {{
    padding: 8px 0;
    font-size: 9.5pt;
    border-bottom: 1px solid {BORDER_LIGHT};
    vertical-align: top;
}}

.cover-meta-table td:first-child {{
    color: {TEXT_MUTED};
    font-weight: 500;
    width: 140px;
    padding-right: 16px;
}}

.cover-meta-table td:last-child {{
    color: {TEXT_COLOR};
    font-weight: 400;
}}

.cover-footer {{
    position: absolute;
    bottom: 20mm;
    left: 22mm;
    right: 22mm;
}}

.cover-footer-line {{
    border-top: 1px solid {BORDER};
    padding-top: 12px;
    display: flex;
    justify-content: space-between;
}}

.cover-company {{
    font-size: 11pt;
    font-weight: 700;
    color: {PRIMARY_DARK};
    letter-spacing: 0.5px;
}}

.cover-cert {{
    font-size: 8.5pt;
    color: {TEXT_MUTED};
    margin-top: 4px;
}}

/* ===== CONTENT AREA (starts on page 2+) ===== */
.content {{
    padding-top: 0;
}}

/* ===== HEADINGS ===== */
h1 {{
    font-size: 20pt;
    font-weight: 700;
    color: {PRIMARY};
    margin-top: 0;
    margin-bottom: 14px;
    padding-top: 30px;
    padding-bottom: 10px;
    border-bottom: 2.5px solid {PRIMARY};
    page-break-before: always;
    page-break-after: avoid;
    letter-spacing: -0.3px;
}}

h1:first-child {{
    page-break-before: avoid;
    margin-top: 0;
    padding-top: 0;
}}

h2 {{
    font-size: 14pt;
    font-weight: 600;
    color: {PRIMARY_DARK};
    margin-top: 28px;
    margin-bottom: 10px;
    padding-bottom: 6px;
    border-bottom: 1.5px solid {ACCENT};
    page-break-after: avoid;
}}

h3 {{
    font-size: 11.5pt;
    font-weight: 600;
    color: {PRIMARY};
    margin-top: 20px;
    margin-bottom: 8px;
    page-break-after: avoid;
}}

h4 {{
    font-size: 10.5pt;
    font-weight: 600;
    color: {TEXT_COLOR};
    margin-top: 16px;
    margin-bottom: 6px;
    page-break-after: avoid;
}}

/* ===== PARAGRAPHS ===== */
p {{
    margin-bottom: 8px;
    text-align: left;
    orphans: 3;
    widows: 3;
}}

/* ===== TABLES ===== */
table {{
    width: 100%;
    border-collapse: collapse;
    margin: 14px 0 18px 0;
    font-size: 8.5pt;
    page-break-inside: auto;
    border: 1px solid {BORDER};
}}

thead {{
    display: table-header-group;
}}

tr {{
    page-break-inside: avoid;
    page-break-after: auto;
}}

th {{
    background-color: {PRIMARY};
    color: {WHITE};
    font-weight: 600;
    text-align: left;
    padding: 7px 10px;
    font-size: 8.5pt;
    letter-spacing: 0.2px;
    border: none;
}}

td {{
    padding: 6px 10px;
    border-bottom: 1px solid {BORDER_LIGHT};
    border-right: 1px solid {BORDER_LIGHT};
    vertical-align: top;
    line-height: 1.45;
}}

td:last-child {{
    border-right: none;
}}

tr:nth-child(even) td {{
    background-color: {BG_LIGHT};
}}

/* ===== CODE ===== */
code {{
    font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace;
    font-size: 8pt;
    background-color: {BG_LIGHT};
    padding: 1px 4px;
    border-radius: 3px;
    border: 1px solid {BORDER_LIGHT};
    color: {PRIMARY_DARK};
}}

pre {{
    background-color: #1e1e2e;
    color: #cdd6f4;
    padding: 12px 14px;
    border-radius: 5px;
    font-size: 7.5pt;
    line-height: 1.5;
    overflow-x: auto;
    margin: 10px 0 14px 0;
    border-left: 3px solid {ACCENT};
    page-break-inside: avoid;
}}

pre code {{
    background-color: transparent;
    color: #cdd6f4;
    padding: 0;
    border: none;
    font-size: 7.5pt;
}}

/* ===== LISTS ===== */
ul, ol {{
    margin: 6px 0 10px 0;
    padding-left: 20px;
}}

li {{
    margin-bottom: 3px;
    line-height: 1.5;
}}

li > ul, li > ol {{
    margin-top: 3px;
    margin-bottom: 3px;
}}

/* ===== HORIZONTAL RULE ===== */
hr {{
    border: none;
    border-top: 1px solid {BORDER};
    margin: 24px 0;
}}

/* ===== STRONG ===== */
strong {{
    font-weight: 600;
    color: {TEXT_COLOR};
}}

/* ===== LINKS ===== */
a {{
    color: {ACCENT_DARK};
    text-decoration: none;
    font-weight: 500;
}}

/* ===== BLOCKQUOTE ===== */
blockquote {{
    border-left: 3px solid {ACCENT};
    background-color: {ACCENT_LIGHT};
    padding: 10px 14px;
    margin: 10px 0;
    font-size: 9.5pt;
    color: {TEXT_SECONDARY};
    border-radius: 0 4px 4px 0;
}}

blockquote p {{
    margin: 3px 0;
}}

/* ===== TOC ===== */
.toc {{
    background-color: {WHITE};
    padding: 0;
    margin: 0 0 24px 0;
    page-break-after: always;
}}

.toc ul {{
    list-style: none;
    padding-left: 0;
    margin: 0;
}}

.toc li {{
    padding: 0;
    margin: 0;
}}

/* All TOC links: block layout with page number */
.toc a {{
    display: block;
    text-decoration: none;
    color: {TEXT_COLOR};
    padding: 4px 0;
    border-bottom: 1px solid {BORDER_LIGHT};
    position: relative;
    padding-right: 45px;
}}

.toc a::after {{
    content: target-counter(attr(href url), page);
    position: absolute;
    right: 0;
    color: {PRIMARY};
    font-weight: 600;
}}

/* Level 1 TOC entries (chapters) */
.toc > ul > li {{
    margin-bottom: 0;
}}

.toc > ul > li > a {{
    font-weight: 600;
    font-size: 10.5pt;
    padding: 6px 45px 6px 0;
    color: {TEXT_COLOR};
    border-bottom: 1px solid {BORDER};
}}

.toc > ul > li > a::after {{
    font-size: 10.5pt;
}}

/* Level 2 TOC entries */
.toc > ul > li > ul {{
    padding-left: 18px;
}}

.toc > ul > li > ul > li > a {{
    font-weight: 400;
    font-size: 9.5pt;
    color: {TEXT_SECONDARY};
    padding: 3px 45px 3px 0;
    border-bottom: 1px dotted {BORDER_LIGHT};
}}

.toc > ul > li > ul > li > a::after {{
    font-weight: 400;
    font-size: 9.5pt;
    color: {TEXT_MUTED};
}}

/* Hide level 3+ in TOC */
.toc > ul > li > ul > li > ul {{
    display: none;
}}

/* ===== PAGE BREAKS ===== */
.page-break {{
    page-break-before: always;
}}

/* ===== PRINT OPTIMIZATIONS ===== */
img {{
    max-width: 100%;
}}

/* Content screenshots rendered as tidy, centered figures (cover logo unaffected). */
.content img {{
    display: block;
    max-width: 100%;
    max-height: 112mm;
    margin: 10px auto 18px auto;
    border: 1px solid {BORDER};
    border-radius: 10px;
    box-shadow: 0 4px 16px rgba(20, 40, 80, 0.12);
    page-break-inside: avoid;
}}

/* A touch tighter so short sections don't leave big gaps. */
.content h3 {{ margin-top: 16px; }}
.content h4 {{ margin-top: 12px; }}
.content ul, .content ol {{ margin-top: 4px; margin-bottom: 8px; }}
.content > p {{ margin-top: 4px; }}
"""


def extract_metadata(md_text: str) -> dict:
    """Extract title and metadata from markdown front matter."""
    lines = md_text.strip().split("\n")
    meta = {"title": "", "fields": [], "content_start": 0}

    # First line should be # Title
    if lines and lines[0].startswith("# "):
        meta["title"] = lines[0][2:].strip()

    # Extract bold metadata fields (e.g. **Version:** 1.0)
    i = 1
    for i, line in enumerate(lines[1:], 1):
        stripped = line.strip()
        if stripped.startswith("**") and ":**" in stripped:
            # Parse **Key:** Value
            match = re.match(r'\*\*(.+?):\*\*\s*(.*)', stripped)
            if match:
                meta["fields"].append((match.group(1), match.group(2)))
        elif stripped == "---":
            meta["content_start"] = i + 1
            break
        elif stripped == "":
            continue
        else:
            meta["content_start"] = i
            break

    return meta


def build_cover_page(meta: dict, doc_info: dict) -> str:
    """Build HTML for the cover page."""
    meta_rows = ""
    for key, value in meta["fields"]:
        meta_rows += f"""
        <tr>
            <td>{key}</td>
            <td>{value}</td>
        </tr>"""

    # Add company info
    meta_rows += """
        <tr>
            <td>Herausgeber</td>
            <td>mindsquare AG</td>
        </tr>
        <tr>
            <td>Zertifizierung</td>
            <td>ISO 27001</td>
        </tr>"""

    return f"""
    <div class="cover-page">
        <div class="cover-top-bar">
            <div style="position: absolute; right: 22mm; top: 10mm; text-align: right;">
                <img src="Mindsquare_Logo.png" style="height: 30px;" alt="mindsquare" />
            </div>
        </div>
        <div class="cover-accent-line"></div>
        <div class="cover-body">
            <div class="cover-doc-type">{doc_info.get('doc_type', 'Dokumentation')}</div>
            <div class="cover-title">{meta['title']}</div>
            <div class="cover-subtitle">{doc_info.get('subtitle', '')}</div>
            <table class="cover-meta-table">
                {meta_rows}
            </table>
        </div>
        <div class="cover-footer">
            <div class="cover-footer-line">
                <div>
                    <div class="cover-company">mindsquare AG</div>
                    <div class="cover-cert">ISO 27001 zertifiziert</div>
                </div>
            </div>
        </div>
    </div>
    """


def preprocess_markdown(text: str) -> str:
    """Ensure blank lines before lists so the markdown parser renders them correctly.

    The Python markdown library requires a blank line between a paragraph and a
    list.  Without it, list items get rendered as inline text instead of <ol>/<ul>.
    """
    lines = text.split("\n")
    result = []
    in_code_block = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track fenced code blocks – don't touch anything inside them
        if stripped.startswith("```"):
            in_code_block = not in_code_block

        if not in_code_block and i > 0:
            prev = lines[i - 1].strip()
            is_list_item = (
                re.match(r'^\d+\.\s', stripped)  # ordered list
                or re.match(r'^[-*+]\s', stripped)  # unordered list
            )
            prev_is_non_blank = prev != ""
            prev_is_not_list = not (
                re.match(r'^\d+\.\s', prev)
                or re.match(r'^[-*+]\s', prev)
            )
            if is_list_item and prev_is_non_blank and prev_is_not_list:
                result.append("")  # inject blank line

        result.append(line)

    return "\n".join(result)


def md_to_html(md_path: str, doc_info: dict) -> str:
    """Convert markdown file to HTML with professional cover page."""
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Extract metadata for cover page
    meta = extract_metadata(md_text)

    # Get content after metadata (skip title and front matter)
    lines = md_text.strip().split("\n")
    content_md = "\n".join(lines[meta["content_start"]:])

    # Fix lists that directly follow paragraphs (missing blank line)
    content_md = preprocess_markdown(content_md)

    # Convert remaining markdown to HTML
    extensions = [
        "tables",
        "fenced_code",
        "codehilite",
        "toc",
        "attr_list",
        "md_in_html",
    ]

    extension_configs = {
        "toc": {
            "title": "",       # No auto-generated title (we use h2 from markdown)
            "toc_depth": "2-3",  # Only h2 and h3 in TOC
        }
    }

    html_body = markdown.markdown(
        content_md, extensions=extensions, extension_configs=extension_configs
    )

    # Build cover page
    cover_html = build_cover_page(meta, doc_info)

    html_doc = f"""<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <style>
    {CSS_TEMPLATE}
    </style>
</head>
<body>
    {cover_html}
    <div class="content">
    {html_body}
    </div>
</body>
</html>"""

    return html_doc


def generate_pdf(md_path: str, pdf_path: str, doc_info: dict):
    """Generate PDF from markdown file."""
    print(f"  Converting {os.path.basename(md_path)} -> {os.path.basename(pdf_path)}")

    html_content = md_to_html(md_path, doc_info)

    # Debug: save HTML for inspection
    html_debug_path = pdf_path.replace(".pdf", ".html")
    with open(html_debug_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    HTML(string=html_content, base_url=os.path.dirname(md_path)).write_pdf(pdf_path)

    file_size = os.path.getsize(pdf_path) / 1024
    print(f"  -> {pdf_path} ({file_size:.0f} KB)")

    # Clean up debug HTML
    os.remove(html_debug_path)


def main():
    docs_dir = os.path.dirname(os.path.abspath(__file__))

    files = [
        ("README.md", "AI-Employee-Benutzerhandbuch.pdf"),
    ]

    print("=" * 60)
    print("  VoiceAI Hörmann - PDF Documentation Generator")
    print(f"  Brand: mindsquare AG ({PRIMARY} | {ACCENT})")
    print("=" * 60)
    print()

    for md_file, pdf_file in files:
        md_path = os.path.join(docs_dir, md_file)
        pdf_path = os.path.join(docs_dir, pdf_file)

        if not os.path.exists(md_path):
            print(f"  SKIP: {md_file} not found")
            continue

        doc_info = DOC_META.get(md_file, {
            "subtitle": "",
            "doc_type": "Dokumentation",
        })

        generate_pdf(md_path, pdf_path, doc_info)

    print()
    print("  Done! All PDFs generated.")
    print("=" * 60)


if __name__ == "__main__":
    main()
