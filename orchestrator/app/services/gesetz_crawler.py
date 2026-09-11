"""Gesetze-Crawler — haelt deutsches Bundesrecht UND kuratiertes EU-Recht
taeglich aktuell und semantisch durchsuchbar (Kundenwunsch: Gesetze crawlen,
aktuell halten, JEDEM Agenten via MCP zugaenglich machen — mit explizitem
Schwerpunkt auf KI-/Agenten-relevantes Recht: EU AI Act, DSGVO und
angrenzendes EU-Digitalrecht).

**Deutsches Bundesrecht:** gesetze-im-internet.de/gii-toc.xml — der offizielle,
maschinenlesbare Gesamtindex des Bundesministeriums der Justiz. Jeder Eintrag
verlinkt ein xml.zip mit dem vollstaendigen Normtext (Schema:
<dokumente><norm>...). Brain-Label ``__gesetze_de__``.

**EU-Recht (kuratiert, nicht vollstaendig):** eur-lex.europa.eu selbst blockt
automatisierte Abrufe hinter einer AWS-WAF-JS-Challenge (getestet, 202 ohne
Inhalt). Der tatsaechliche Weg ist die CELLAR-RESTful-Schnittstelle unter
``publications.europa.eu`` — ein separater, fuer maschinellen Zugriff gedachter
Host ohne diese Sperre: ``http://publications.europa.eu/resource/celex/<CELEX>``
mit ``Accept: application/xhtml+xml`` + ``Accept-Language: deu`` liefert den
vollstaendigen deutschen Normtext als valides XHTML (ELI-Struktur,
``<div id="art_N">`` pro Artikel). Ein vollstaendiger EUR-Lex-Crawl (wie beim
Bundesrecht) waere ein eigenes, deutlich groesseres Projekt (CELLAR-SPARQL-
Discovery, mehrsprachige Dokumenttypen) — deshalb hier bewusst eine kuratierte,
von Hand gepflegte Liste statt eines automatischen Gesamtindex. Brain-Label
``__gesetze_eu__``.

Beide Quellen schreiben ihre Normen als Markdown-Dateien unter /shared/gesetze/
(derselbe, von Orchestrator UND Agenten gemeinsam gemountete Pfad wie die
Second-Brain-Vaults) und indizieren sie ueber dieselbe Chunk+Embed-Pipeline wie
ein Second Brain (vault_indexer.index_file) — ``brain_label`` ist ein reiner
String ohne FK, es braucht keine begleitende SecondBrain-DB-Zeile. Die Suche
laeuft ueber genau denselben vault_search.hybrid_search-Pfad (echtes
pgvector-Semantik + Keyword, keine reine Grep-Suche).
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

EU_BRAIN_LABEL = "__gesetze_eu__"
EU_HOST_PATH = "/shared/gesetze/eu"
EU_CELLAR_URL = "http://publications.europa.eu/resource/celex/{celex}"
_EU_XHTML_NS = "{http://www.w3.org/1999/xhtml}"

#: Kuratiert statt automatisch entdeckt (siehe Moduldoc) — bewusst genau das,
#: was der Kunde nannte: EU AI Act, DSGVO, und das unmittelbar angrenzende
#: EU-Digitalrecht mit klarem KI-/Agenten-Bezug (Plattformpflichten, Datenzugang).
#: (celex, kurzname) — kurzname wird zur Dateikennung und zum jurabk-Fallback.
EU_CURATED_LAWS: list[tuple[str, str]] = [
    ("32024R1689", "EU-AI-Act"),
    ("32016R0679", "DSGVO"),
    ("32022R2065", "DSA"),
    ("32023R2854", "Data-Act"),
]


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


def parse_eu_regulation_xhtml(xhtml_bytes: bytes, fallback_name: str) -> dict | None:
    """Parse one EUR-Lex/CELLAR ELI-XHTML regulation into the same shape
    ``parse_norm_xml`` returns, so ``render_markdown`` needs no EU-specific
    branch. Articles are ``<div id="art_N">`` (never "art_N.tit_1" — that id
    suffix belongs to the article's own title, not a separate article).
    """
    root = ElementTree.fromstring(xhtml_bytes)
    ns = _EU_XHTML_NS

    title_parts: list[str] = []
    for div in root.iter(f"{ns}div"):
        if div.get("class") == "eli-main-title":
            for p in div.iter(f"{ns}p"):
                t = " ".join("".join(p.itertext()).split())
                if t:
                    title_parts.append(t)
            break
    title = " ".join(title_parts) or fallback_name

    paragraphs: list[dict] = []
    for div in root.iter(f"{ns}div"):
        did = div.get("id", "")
        if not did.startswith("art_") or "." in did:
            continue  # only the article itself, not its nested "art_N.tit_1" title div
        num_p = div.find(f"{ns}p")
        enbez = " ".join("".join(num_p.itertext()).split()) if num_p is not None else did
        titel = ""
        title_div = div.find(f"{ns}div[@class='eli-title']")
        if title_div is not None:
            tp = title_div.find(f"{ns}p")
            if tp is not None:
                titel = " ".join("".join(tp.itertext()).split())
        texts: list[str] = []
        for p in div.iter(f"{ns}p"):
            if p.get("class") in ("oj-ti-art", "oj-sti-art"):
                continue
            t = " ".join("".join(p.itertext()).split())
            if t:
                texts.append(t)
        text = "\n\n".join(texts)
        if enbez and text:
            paragraphs.append({"enbez": enbez, "titel": titel, "text": text})

    if not paragraphs:
        return None
    return {"jurabk": fallback_name, "title": title, "paragraphs": paragraphs}


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
    """Crawls gesetze-im-internet.de (DE) and the curated EU-law list daily,
    indexing both into vault_chunks under their own brain_label."""

    def __init__(self):
        self.last_crawled_at: str | None = None
        self.law_count: int = 0
        self.eu_last_crawled_at: str | None = None
        self.eu_law_count: int = 0

    async def run(self) -> None:
        """Background loop — EU first (fast, higher priority), then the full
        DE crawl (slow), then daily."""
        while True:
            # EU zuerst: eine Handvoll kuratierter Normen, in Sekunden fertig
            # — genau das, was der Kunde als aktuelle Prioritaet nannte (EU AI
            # Act, DSGVO). Das volle Bundesrecht (6000+ Normen, mehrere
            # Minuten) waere sonst bei jedem Neustart im Weg.
            try:
                await self.crawl_eu()
            except Exception as e:
                logger.error("Gesetz crawler (EU) error: %s", e, exc_info=True)
            try:
                await self.crawl()
            except Exception as e:
                logger.error("Gesetz crawler (DE) error: %s", e, exc_info=True)
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

    async def crawl_eu(self) -> int:
        """Fetch the curated EU-law list, index every one, return the count
        actually written. Small, fixed list — no discovery/pagination needed."""
        from app.core import vault
        from app.db.session import resilient_session
        from app.services import vault_indexer

        headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "deu"}
        written = 0
        async with httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT, headers=headers, follow_redirects=True
        ) as client:
            for celex, kurzname in EU_CURATED_LAWS:
                try:
                    resp = await client.get(
                        EU_CELLAR_URL.format(celex=celex),
                        headers={"Accept": "application/xhtml+xml"},
                    )
                    if resp.status_code != 200:
                        logger.debug("Gesetz crawler (EU): %s -> HTTP %s", celex, resp.status_code)
                        continue
                    law = parse_eu_regulation_xhtml(resp.content, kurzname)
                    if not law:
                        continue
                    md = render_markdown(law)
                    rel_path = f"{celex}.md"
                    vault.write_file(EU_HOST_PATH, rel_path, md)
                    async with resilient_session() as db:
                        await vault_indexer.index_file(db, EU_BRAIN_LABEL, EU_HOST_PATH, rel_path)
                    written += 1
                except Exception as e:
                    logger.debug("Gesetz crawler (EU): %s (%s) failed: %s", celex, kurzname, e)

        from datetime import datetime

        self.eu_last_crawled_at = datetime.now(UTC).isoformat()
        self.eu_law_count = written
        logger.info("Gesetz crawler (EU): %d Normen indiziert", written)
        return written
