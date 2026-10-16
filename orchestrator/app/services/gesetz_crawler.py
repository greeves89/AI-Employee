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
import os
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

from app.core.verbindungswiederholung import (
    PAUSEN_HINTERGRUND_S,
    Netzwache,
    lauf_unvollstaendig,
    mit_verbindungswiederholung,
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


_AN = {"1", "true", "yes", "on", "ja"}
_AUS = {"0", "false", "no", "off", "nein"}


def crawler_aktiv(einstellung: str | None, bestand_vorhanden: bool) -> tuple[bool, str]:
    """Soll der Gesetze-Crawler laufen? Reine Entscheidung (#890), gibt
    (an, Grund) zurueck. Ausdrueckliches true/false gewinnt; ohne Einstellung
    laeuft er nur, wenn auf der Anlage schon Gesetzesdaten liegen — bestehende
    Anlagen verlieren nichts, neue Anlagen bleiben ohne die Dauerlast."""
    wert = (einstellung or "").strip().lower()
    if wert in _AN:
        return True, "GESETZE_CRAWLER_ENABLED=true"
    if wert in _AUS:
        return False, "GESETZE_CRAWLER_ENABLED=false"
    if bestand_vorhanden:
        return True, "GESETZE_CRAWLER_ENABLED nicht gesetzt, aber Gesetzesdaten vorhanden (Bestandsschutz)"
    return False, "GESETZE_CRAWLER_ENABLED nicht gesetzt und keine Gesetzesdaten vorhanden (Standard: aus)"


async def bestand_vorhanden() -> bool:
    """Liegen auf der Anlage schon indizierte Gesetze (Bund oder EU)?"""
    from sqlalchemy import text as sa_text

    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        row = (
            await db.execute(
                sa_text("SELECT 1 FROM vault_chunks WHERE brain_label IN (:de, :eu) LIMIT 1"),
                {"de": BRAIN_LABEL, "eu": EU_BRAIN_LABEL},
            )
        ).first()
    return row is not None


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


#: Groesste Datei je Gesetzesteil. Bewusst unter ``vault.MAX_FILE_BYTES``
#: (2 MB): das SGB V allein ist als eine Datei groesser und wurde bei jedem
#: Lauf abgelehnt (#866). Die Grenze hochzusetzen verschoebe das Problem nur
#: bis zum naechsten grossen Gesetz.
TEIL_MAX_BYTES = 1_500_000

_TEIL_RE = re.compile(r"^(?P<basis>.+)\.teil-(?P<nr>[0-9]+)\.md$")


def _teil_pfad(basis: str, nr: int) -> str:
    """Teil 1 behaelt den bisherigen Dateinamen, weitere Teile ``<basis>.teil-N.md``."""
    return f"{basis}.md" if nr == 1 else f"{basis}.teil-{nr}.md"


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def _hart_teilen(text: str, budget: int) -> list[str]:
    """Text in Stuecke von hoechstens ``budget`` Bytes, ohne ein Zeichen zu zerbrechen."""
    stuecke: list[str] = []
    rest = text.encode("utf-8")
    while rest:
        stueck = rest[:budget].decode("utf-8", errors="ignore")
        stuecke.append(stueck)
        rest = rest[len(stueck.encode("utf-8")):]
    return stuecke


def _bloecke(law: dict, budget: int) -> list[str]:
    """Ein Block je Paragraph (``## § N Titel`` + Text). Ein Paragraph, der
    allein das Budget sprengt, wird an Absatzgrenzen (notfalls hart) geteilt;
    die Folgestuecke tragen ``(Fortsetzung)`` in der Ueberschrift, damit jeder
    Treffer in der Suche weiss, wohin er gehoert."""
    bloecke: list[str] = []
    for p in law["paragraphs"]:
        heading = f"## {p['enbez']}" + (f" {p['titel']}" if p["titel"] else "")
        block = f"{heading}\n\n{p['text']}\n"
        if _bytes(block) <= budget:
            bloecke.append(block)
            continue
        fortsetzung = f"## {p['enbez']} (Fortsetzung)"
        kopf_max = max(_bytes(heading), _bytes(fortsetzung)) + 3
        absaetze: list[str] = []
        for absatz in p["text"].split("\n\n"):
            absaetze.extend(_hart_teilen(absatz, budget - kopf_max))
        aktuell: list[str] = []
        for absatz in absaetze:
            if aktuell and _bytes("\n\n".join(aktuell + [absatz])) + kopf_max > budget:
                kopf = heading if not bloecke or not bloecke[-1].startswith(heading) else fortsetzung
                bloecke.append(f"{kopf}\n\n" + "\n\n".join(aktuell) + "\n")
                aktuell = []
            aktuell.append(absatz)
        if aktuell:
            kopf = heading if not bloecke or not bloecke[-1].startswith(heading) else fortsetzung
            bloecke.append(f"{kopf}\n\n" + "\n\n".join(aktuell) + "\n")
    return bloecke


def render_markdown_teile(law: dict, max_bytes: int | None = None) -> list[str]:
    """Wie ``render_markdown``, aber in Teilen von hoechstens ``max_bytes``.

    Passt das Gesetz in eine Datei, ist das Ergebnis genau ``[render_markdown(law)]``
    — fuer alle anderen Gesetze aendert sich nichts. Sonst entstehen mehrere
    Teile mit der Ueberschrift ``# <jurabk> — <Titel> (Teil i von n)``; jeder
    Paragraph steht genau einmal, in der Originalreihenfolge.
    """
    max_bytes = max_bytes or TEIL_MAX_BYTES
    ganz = render_markdown(law)
    if _bytes(ganz) <= max_bytes:
        return [ganz]

    titel = f"# {law['jurabk']} — {law['title']}".rstrip(" —")
    # Platz fuer die laengste denkbare Teil-Ueberschrift freihalten.
    kopf_max = _bytes(f"{titel} (Teil 9999 von 9999)\n\n")
    budget = max_bytes - kopf_max

    gruppen: list[list[str]] = [[]]
    for block in _bloecke(law, budget):
        gruppe = gruppen[-1]
        if gruppe and _bytes("\n".join(gruppe + [block])) > budget:
            gruppen.append([block])
        else:
            gruppe.append(block)
    n = len(gruppen)
    return [f"{titel} (Teil {i} von {n})\n\n" + "\n".join(g) for i, g in enumerate(gruppen, 1)]


def _vorhandene_teile(host_path: str) -> dict[str, set[int]]:
    """Welche Folgeteile (Teil 2 und hoeher) liegen schon im Ordner? Einmal je Lauf
    gelesen, damit verwaiste Teile eines frueheren, laengeren Stands auffallen."""
    try:
        namen = os.listdir(host_path)
    except FileNotFoundError:
        return {}
    teile: dict[str, set[int]] = {}
    for name in namen:
        m = _TEIL_RE.match(name)
        if m:
            teile.setdefault(m.group("basis"), set()).add(int(m.group("nr")))
    return teile


async def _gesetz_schreiben(
    host_path: str, brain_label: str, basis: str, law: dict, vorhandene: dict[str, set[int]],
) -> None:
    """Ein Gesetz als eine oder mehrere Dateien schreiben und indizieren;
    Folgeteile, die es nicht mehr gibt, aus Ordner und Index entfernen."""
    from app.core import vault
    from app.db.session import resilient_session
    from app.services import vault_indexer

    teile = render_markdown_teile(law)
    for nr, md in enumerate(teile, 1):
        rel_path = _teil_pfad(basis, nr)
        vault.write_file(host_path, rel_path, md)
        async with resilient_session() as db:
            await vault_indexer.index_file(db, brain_label, host_path, rel_path)
    for nr in sorted(n for n in vorhandene.get(basis, ()) if n > len(teile)):
        rel_path = _teil_pfad(basis, nr)
        try:
            vault.delete_file(host_path, rel_path)
        except FileNotFoundError:
            pass
        async with resilient_session() as db:
            await vault_indexer.remove_file(db, brain_label, rel_path)


class _AusfallZaehler:
    """Zaehlt Fehlschlaege eines Crawl-Laufs und meldet sie EINMAL am Ende.

    Warum ueberhaupt: die beiden Crawl-Schleifen haben jeden Fehler einzeln
    auf ``debug`` gelegt und weitergemacht. Bei einem dauerhaften Mangel --
    #834: ``vault_chunks`` ohne ``embedding``/``ts``, jedes Einfuegen scheitert
    -- laeuft der Crawler dann endlos ohne eine einzige sichtbare Zeile: ein
    Kern auf Anschlag, null Ergebnis, nichts im Betriebslog. Ein Fehlschlag
    ist normal (eine Norm fehlt), ALLE sind ein Befund; deshalb eine
    Sammelmeldung mit Anzahl und erstem Grund statt Zeile fuer Zeile.
    """

    #: So viele Kennungen nennt die Meldung hoechstens.
    MAX_KENNUNGEN = 5

    def __init__(self, bereich: str, wache: Netzwache | None = None):
        self.bereich = bereich
        self.anzahl = 0
        self.erster_grund: str | None = None
        self.kennungen: list[str] = []
        #: Trennt den Netzaussetzer vom stehenden Mangel (#746). Die Entscheidung,
        #: WAS ein Verbindungsfehler ist, steht genau einmal — in der Netzwache.
        self.wache = wache or Netzwache(bereich)

    @property
    def verbindungsausfaelle(self) -> int:
        """Von den Fehlschlaegen an der VERBINDUNG gescheitert, nicht am Inhalt."""
        return self.wache.anzahl

    def melde(self, kennung: str, fehler: BaseException) -> None:
        self.anzahl += 1
        self.kennungen.append(kennung)
        self.wache.melde(fehler)
        if self.erster_grund is None:
            self.erster_grund = f"{kennung}: {type(fehler).__name__}: {fehler}"

    def bericht(self, geschrieben: int, seit_laeufen: int = 1) -> str | None:
        """Sammelmeldung, oder ``None`` wenn nichts zu melden ist.

        ``seit_laeufen`` > 1: dieselben Normen scheitern seit so vielen Laeufen
        in Folge — ein stehender Mangel, kein Netzaussetzer (#866)."""
        if not self.anzahl:
            return None
        genannt = ", ".join(self.kennungen[: self.MAX_KENNUNGEN])
        if len(self.kennungen) > self.MAX_KENNUNGEN:
            genannt += " …"
        text = (
            f"{self.bereich}: {self.anzahl} Normen fehlgeschlagen, "
            f"{geschrieben} geschrieben — erster Grund: {self.erster_grund} — betroffen: {genannt}"
        )
        if seit_laeufen > 1:
            text += f" — unverändert seit {seit_laeufen} Läufen"
        return text


def _stehend(vorher: tuple[frozenset, int] | None, jetzt: _AusfallZaehler) -> tuple[frozenset, int] | None:
    """Neuer Stand fuer die Erkennung stehender Fehlschlaege: (Kennungen, Laeufe in Folge)."""
    if not jetzt.anzahl:
        return None
    kennungen = frozenset(jetzt.kennungen)
    if vorher and vorher[0] == kennungen:
        return kennungen, vorher[1] + 1
    return kennungen, 1


class GesetzCrawlerService:
    """Crawls gesetze-im-internet.de (DE) and the curated EU-law list daily,
    indexing both into vault_chunks under their own brain_label."""

    def __init__(self, aktiv: bool = True):
        self.aktiv = aktiv
        self.last_crawled_at: str | None = None
        self.law_count: int = 0
        self.eu_last_crawled_at: str | None = None
        self.eu_law_count: int = 0
        # Bereich -> (scheiternde Kennungen, Laeufe in Folge): stehende Fehlschlaege
        self._stehend: dict[str, tuple[frozenset, int] | None] = {}

    async def run(self) -> None:
        """Background loop — EU first (fast, higher priority), then the full
        DE crawl (slow), then daily."""
        if not self.aktiv:
            return
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

    @staticmethod
    def _netzaussetzer(ausfaelle: _AusfallZaehler, geschrieben: int, bisher: int) -> bool:
        """Darf der Frischestempel dieses Laufs NICHT weiterruecken?

        ``/api/compliance`` zeigt Stempel und Normenzahl zusammen an. "Gerade
        gecrawlt, 0 Normen" liest sich wie ein leerer Bestand statt wie ein
        Netzaussetzer (#746) — und "gerade gecrawlt, 40 Normen" statt 6123 ist
        dieselbe Falschaussage, nur unauffaelliger: ein Aussetzer MITTEN im Lauf
        ueber rund 6000 Normen bricht ihn nicht ab, sondern kuerzt ihn. Deshalb
        entscheidet nicht "nichts geschrieben", sondern "am Netz gescheitert UND
        schlechter als der bisherige Stand". Der alte Stempel ist dann die
        ehrlichere Angabe: er sagt, wann zuletzt wirklich etwas geholt wurde.
        """
        return lauf_unvollstaendig(
            erzeugt=geschrieben,
            bisher=bisher,
            verbindungsausfaelle=ausfaelle.verbindungsausfaelle,
        )

    def _bericht(self, ausfaelle: _AusfallZaehler, geschrieben: int) -> str | None:
        stand = _stehend(self._stehend.get(ausfaelle.bereich), ausfaelle)
        self._stehend[ausfaelle.bereich] = stand
        return ausfaelle.bericht(geschrieben, stand[1] if stand else 1)

    async def _fetch_toc(self, client: httpx.AsyncClient) -> list[tuple[str, str]]:
        """Return [(title, slug), ...] from gii-toc.xml."""
        resp = await mit_verbindungswiederholung(
            lambda: client.get(TOC_URL),
            zweck="Gesetz-Crawler (Inhaltsverzeichnis)",
            pausen=PAUSEN_HINTERGRUND_S,
        )
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
        resp = await mit_verbindungswiederholung(
            lambda: client.get(url, follow_redirects=True),
            zweck=f"Gesetz-Crawler ({slug})",
            pausen=PAUSEN_HINTERGRUND_S,
        )
        if resp.status_code != 200:
            return None
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not names:
                return None
            return parse_norm_xml(zf.read(names[0]))

    async def crawl(self) -> int:
        """Fetch the full TOC, index every law, return the count actually written."""
        headers = {"User-Agent": "AI-Employee-Gesetze-Crawler/1.0"}
        written = 0
        ausfaelle = _AusfallZaehler("Gesetz crawler")
        vorhandene = _vorhandene_teile(HOST_PATH)
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, headers=headers) as client:
            toc = await self._fetch_toc(client)
            logger.info("Gesetz crawler: %d Normen im Index", len(toc))

            for title, slug in toc:
                # Hoert die Verbindung gar nicht mehr auf zu scheitern, wird hier
                # abgebrochen: sonst wartet der Lauf 40 s je Norm und blockiert
                # ueber Tage den nachgelagerten EU-Lauf (HINTERGRUND_ABBRUCH_NACH).
                if ausfaelle.wache.abbrechen:
                    break
                try:
                    law = await self._fetch_one(client, slug)
                    if not law:
                        continue
                    await _gesetz_schreiben(HOST_PATH, BRAIN_LABEL, slug, law, vorhandene)
                    written += 1
                    ausfaelle.wache.erfolg()
                except Exception as e:
                    logger.debug("Gesetz crawler: %s (%s) failed: %s", slug, title, e)
                    ausfaelle.melde(slug, e)

        from datetime import datetime

        if self._netzaussetzer(ausfaelle, written, self.law_count):
            logger.error(
                "Gesetz crawler: nur %d von bisher %d Normen geschrieben, %d von %d "
                "Fehlschlaegen an der Verbindung (Namensaufloesung/Netz) — "
                "Frischestempel bleibt auf %s.",
                written, self.law_count, ausfaelle.verbindungsausfaelle,
                ausfaelle.anzahl, self.last_crawled_at or "(noch nie gesetzt)",
            )
        else:
            self.last_crawled_at = datetime.now(UTC).isoformat()
            self.law_count = written
        bericht = self._bericht(ausfaelle, written)
        if bericht:
            logger.warning(bericht)
        logger.info("Gesetz crawler: %d Normen indiziert", written)
        return written

    async def crawl_eu(self) -> int:
        """Fetch the curated EU-law list, index every one, return the count
        actually written. Small, fixed list — no discovery/pagination needed."""
        headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "deu"}
        written = 0
        ausfaelle = _AusfallZaehler("Gesetz crawler (EU)")
        vorhandene = _vorhandene_teile(EU_HOST_PATH)
        async with httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT, headers=headers, follow_redirects=True
        ) as client:
            for celex, kurzname in EU_CURATED_LAWS:
                if ausfaelle.wache.abbrechen:
                    break
                try:
                    eu_ziel = EU_CELLAR_URL.format(celex=celex)
                    resp = await mit_verbindungswiederholung(
                        lambda eu_ziel=eu_ziel: client.get(
                            eu_ziel, headers={"Accept": "application/xhtml+xml"},
                        ),
                        zweck=f"Gesetz-Crawler EU ({celex})",
                        pausen=PAUSEN_HINTERGRUND_S,
                    )
                    if resp.status_code != 200:
                        logger.debug("Gesetz crawler (EU): %s -> HTTP %s", celex, resp.status_code)
                        continue
                    law = parse_eu_regulation_xhtml(resp.content, kurzname)
                    if not law:
                        continue
                    await _gesetz_schreiben(EU_HOST_PATH, EU_BRAIN_LABEL, celex, law, vorhandene)
                    written += 1
                    ausfaelle.wache.erfolg()
                except Exception as e:
                    logger.debug("Gesetz crawler (EU): %s (%s) failed: %s", celex, kurzname, e)
                    ausfaelle.melde(celex, e)

        from datetime import datetime

        if self._netzaussetzer(ausfaelle, written, self.eu_law_count):
            logger.error(
                "Gesetz crawler (EU): nur %d von bisher %d Normen geschrieben, %d von %d "
                "Fehlschlaegen an der Verbindung (Namensaufloesung/Netz) — "
                "Frischestempel bleibt auf %s.",
                written, self.eu_law_count, ausfaelle.verbindungsausfaelle,
                ausfaelle.anzahl, self.eu_last_crawled_at or "(noch nie gesetzt)",
            )
        else:
            self.eu_last_crawled_at = datetime.now(UTC).isoformat()
            self.eu_law_count = written
        bericht = self._bericht(ausfaelle, written)
        if bericht:
            logger.warning(bericht)
        logger.info("Gesetz crawler (EU): %d Normen indiziert", written)
        return written
