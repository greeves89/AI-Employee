"""Gesetze-Crawler — holt deutsches Bundesrecht taeglich und haelt es semantisch
durchsuchbar (Kundenwunsch aus einem Kundentermin: Gesetze crawlen, aktuell halten,
JEDEM Agenten via MCP zugaenglich machen).

Quelle: gesetze-im-internet.de/gii-toc.xml — der offizielle, maschinenlesbare
Gesamtindex des Bundesministeriums der Justiz. Jeder Eintrag verlinkt ein
xml.zip mit dem vollstaendigen Normtext (Schema: <dokumente><norm>...).

Schreibt jede Norm als eine Markdown-Datei unter /shared/gesetze/de/ (derselbe,
von Orchestrator UND Agenten gemeinsam gemountete Pfad wie die Second-Brain-
Vaults) und indiziert sie ueber dieselbe Chunk+Embed-Pipeline wie ein Second
Brain (vault_indexer.index_file), unter dem reservierten Brain-Label
__gesetze_de__ — ``brain_label`` ist ein reiner String ohne FK, es braucht
keine begleitende SecondBrain-DB-Zeile. Die Suche laeuft ueber genau denselben
vault_search.hybrid_search-Pfad (echtes pgvector-Semantik + Keyword, keine
reine Grep-Suche).
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import zipfile
from datetime import UTC
from xml.etree.ElementTree import (
    Element,  # type only — parsing goes through defusedxml below
)

import httpx
from defusedxml import (
    ElementTree,  # XML from an external source: XXE/billion-laughs hardened
)

logger = logging.getLogger(__name__)

TOC_URL = "https://www.gesetze-im-internet.de/gii-toc.xml"
BRAIN_LABEL = "__gesetze_de__"
HOST_PATH = "/shared/gesetze/de"
CRAWL_INTERVAL = 86400  # taeglich — Gesetzesaenderungen sind selten, aber es soll "aktuell" bleiben
_REQUEST_TIMEOUT = 30.0
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug_from_link(link: str) -> str | None:
    """'http://.../betaeubm_v/xml.zip' -> 'betaeubm_v' — the site's own stable id."""
    m = re.search(r"/([^/]+)/xml\.zip$", link.strip())
    return m.group(1) if m else None


def _clean_text(elem: Element | None) -> str:
    """Flatten a <Content> subtree to plain text, paragraph by paragraph.

    The norm text nests lists (<DL>/<DT>/<DD>/<LA>) inside <P> — itertext()
    collects everything depth-first, so a simple whitespace-normalising join
    keeps enumerations readable without needing to model every tag.
    """
    if elem is None:
        return ""
    parts: list[str] = []
    for p in elem.findall(".//P"):
        text = " ".join("".join(p.itertext()).split())
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def parse_norm_xml(xml_bytes: bytes) -> dict | None:
    """Parse one gesetze-im-internet <dokumente> XML into a structured law.

    Returns {"jurabk": ..., "title": ..., "paragraphs": [{"enbez", "titel", "text"}]}
    or None if the document carries no usable text (e.g. a pure "aufgehoben" stub).
    """
    root = ElementTree.fromstring(xml_bytes)
    norms = root.findall("norm")
    if not norms:
        return None

    head = norms[0].find("metadaten")
    # Titles wrap across lines in the source XML (e.g. langue) — normalise to
    # one line so they don't break the Markdown heading they become.
    jurabk = " ".join((head.findtext("jurabk") or "").split()) if head is not None else ""
    title = " ".join((head.findtext("langue") or "").split()) if head is not None else ""
    if not jurabk:
        return None

    paragraphs: list[dict] = []
    for norm in norms[1:]:
        meta = norm.find("metadaten")
        if meta is None:
            continue
        enbez = " ".join((meta.findtext("enbez") or "").split())
        if not enbez:
            continue  # a Gliederungseinheit (pure section header) — no own text
        titel = " ".join((meta.findtext("titel") or "").split())
        content = norm.find("./textdaten/text/Content")
        text = _clean_text(content)
        if not text:
            continue
        paragraphs.append({"enbez": enbez, "titel": titel, "text": text})

    if not paragraphs:
        return None
    return {"jurabk": jurabk, "title": title, "paragraphs": paragraphs}


def render_markdown(law: dict) -> str:
    """One law -> one Markdown file, headings per §-Norm so chunk_markdown
    (heading-based passage splitting) naturally produces one chunk per
    paragraph, carrying "jurabk > § N Titel" as retrieval context."""
    lines = [f"# {law['jurabk']} — {law['title']}".rstrip(" —"), ""]
    for p in law["paragraphs"]:
        heading = f"## {p['enbez']}" + (f" {p['titel']}" if p["titel"] else "")
        lines.append(heading)
        lines.append("")
        lines.append(p["text"])
        lines.append("")
    return "\n".join(lines)


class GesetzCrawlerService:
    """Crawls gesetze-im-internet.de daily and indexes into vault_chunks."""

    def __init__(self):
        self.last_crawled_at: str | None = None
        self.law_count: int = 0

    async def run(self) -> None:
        """Background loop — crawl on startup, then daily."""
        while True:
            try:
                await self.crawl()
            except Exception as e:
                logger.error("Gesetz crawler error: %s", e, exc_info=True)
            await asyncio.sleep(CRAWL_INTERVAL)

    async def _fetch_toc(self, client: httpx.AsyncClient) -> list[tuple[str, str]]:
        """Return [(title, slug), ...] from gii-toc.xml."""
        resp = await client.get(TOC_URL)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        out: list[tuple[str, str]] = []
        for item in root.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            slug = _slug_from_link(link)
            if slug:
                out.append((title, slug))
        return out

    async def _fetch_one(self, client: httpx.AsyncClient, slug: str) -> dict | None:
        url = f"https://www.gesetze-im-internet.de/{slug}/xml.zip"
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return None
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not names:
                return None
            return parse_norm_xml(zf.read(names[0]))

    async def crawl(self) -> int:
        """Fetch the full TOC, index every law, return the count actually written."""
        from app.core import vault
        from app.db.session import resilient_session
        from app.services import vault_indexer

        headers = {"User-Agent": "AI-Employee-Gesetze-Crawler/1.0"}
        written = 0
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, headers=headers) as client:
            toc = await self._fetch_toc(client)
            logger.info("Gesetz crawler: %d Normen im Index", len(toc))

            for title, slug in toc:
                try:
                    law = await self._fetch_one(client, slug)
                    if not law:
                        continue
                    md = render_markdown(law)
                    rel_path = f"{slug}.md"
                    vault.write_file(HOST_PATH, rel_path, md)
                    async with resilient_session() as db:
                        await vault_indexer.index_file(db, BRAIN_LABEL, HOST_PATH, rel_path)
                    written += 1
                except Exception as e:
                    logger.debug("Gesetz crawler: %s (%s) failed: %s", slug, title, e)

        from datetime import datetime

        self.last_crawled_at = datetime.now(UTC).isoformat()
        self.law_count = written
        logger.info("Gesetz crawler: %d Normen indiziert", written)
        return written
