"""Logik des Egress-Proxys ohne mitmproxy — lokal testbar (orchestrator/tests/test_egress.py).

Das Addon (aie_egress.py) ruft nur diese Funktionen. Gegenstueck im Orchestrator:
orchestrator/app/core/egress.py und orchestrator/app/api/egress.py.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

PLATZHALTER_RE = re.compile(r"aie-geheim-\d{1,10}-[0-9a-f]{12}")
MARKE = "aie-geheim-"
CA_HOST = "aie-egress.internal"

#: Wie lange eine bestaetigte Anmeldung frisch ist — und wie lange sie noch gilt,
#: wenn der Orchestrator gerade nicht antwortet (Neustart beim Update). Ohne das
#: stuende waehrend jedes Neustarts aller HTTPS-Verkehr der Agenten.
ANMELDUNG_FRISCH = 60
ANMELDUNG_NOTFALL = 6 * 3600
WERTE_FRISCH = 30
#: Bestaetigte Werte gelten weiter, solange der Orchestrator nicht antwortet (Neustart
#: auf dem Pi > 30 s) — eine ausdrueckliche Ablehnung gilt dagegen sofort.
WERTE_NOTFALL = 180
#: So lange verdeckt der Proxy einen einmal eingesetzten Wert in allen Antworten an
#: denselben Agenten — auch in spaeteren Abrufen, falls ein Ziel ihn gespeichert hat.
MASKE_DAUER = 24 * 3600
#: Nur ueber diesen Port werden Keys eingesetzt (kein Ausweichen auf Admin-Ports).
KEY_PORT = 443
#: Ausserhalb des globalen Adressraums, aber von ``is_global`` nicht als intern erkannt.
_UEBERSETZT = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"),
               ipaddress.ip_network("2002::/16"))
MAX_KOERPER = 1024 * 1024


class OrchestratorFehler(Exception):
    """Orchestrator nicht erreichbar oder unerwartete Antwort."""


class Abgelehnt(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status = status
        self.text = text


def anmeldung(kopf: str | None) -> tuple[str, str] | None:
    """``Proxy-Authorization: Basic base64(agent_id:token)`` → (agent_id, token)."""
    if not kopf or not kopf.startswith("Basic "):
        return None
    try:
        roh = base64.b64decode(kopf[6:].strip(), validate=True).decode()
    except Exception:  # noqa: BLE001
        return None
    agent_id, sep, token = roh.partition(":")
    if not sep or not agent_id or not token or len(agent_id) > 32 or len(token) > 128:
        return None
    return agent_id, token


def host_passt(host: str, muster: list[str]) -> bool:
    """Wie orchestrator/app/core/egress.host_passt — hier nur, um TLS aufzubrechen.
    Ob ein Key an das Ziel darf, entscheidet allein der Orchestrator."""
    h = (host or "").strip().lower().rstrip(".")
    return bool(h) and h in (muster or [])


def ziel_adresse(host: str, port: int, aufloesen=socket.getaddrinfo) -> str:
    """Oeffentliche IP fuer ``host`` — oder ``Abgelehnt``.

    Der Proxy ist kein Umweg ins Firmennetz: Alle Adressen des Namens muessen
    oeffentlich sein, und verbunden wird mit genau der geprueften (kein zweites
    Aufloesen, das anders ausfallen koennte).
    """
    try:
        infos = aufloesen(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise Abgelehnt(502, f"Ziel nicht auflösbar: {host}") from e
    adressen = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or any(ip in netz for netz in _UEBERSETZT):
            raise Abgelehnt(403, f"Interne Adresse über den Egress-Proxy gesperrt: {host}")
        adressen.append(str(ip))
    if not adressen:
        raise Abgelehnt(502, f"Ziel nicht auflösbar: {host}")
    # IPv4 zuerst: Im Container fehlt oft die IPv6-Route.
    adressen.sort(key=lambda a: ":" in a)
    return adressen[0]


def platzhalter_in(*texte: str) -> set[str]:
    gefunden: set[str] = set()
    for t in texte:
        if t and MARKE in t:
            gefunden.update(PLATZHALTER_RE.findall(t))
    return gefunden


def _basic_entpacken(wert: str) -> str | None:
    if not wert.startswith("Basic "):
        return None
    try:
        return base64.b64decode(wert[6:].strip(), validate=True).decode()
    except Exception:  # noqa: BLE001
        return None


def kopf_platzhalter(name: str, wert: str) -> set[str]:
    """Platzhalter in einem Kopf — auch in ``Authorization: Basic`` (git schickt so)."""
    gefunden = platzhalter_in(wert)
    if name.lower() == "authorization":
        gefunden |= platzhalter_in(_basic_entpacken(wert) or "")
    return gefunden


#: Namen von Adress- und Formular-Parametern, in denen ein Key stehen darf.
ANMELDE_PARAMETER = re.compile(
    r"^(key|api[_-]?key|apikey|token|access[_-]?token|auth|auth[_-]?token|private[_-]?token|"
    r"client[_-]?secret)$", re.IGNORECASE)
FORMULAR = "application/x-www-form-urlencoded"


def _parameter_platzhalter(abfrage: str, wo: str) -> set[str]:
    gefunden: set[str] = set()
    for name, wert in urllib.parse.parse_qsl(abfrage, keep_blank_values=True):
        if not platzhalter_in(name, wert):
            continue
        if ANMELDE_PARAMETER.match(name) and PLATZHALTER_RE.fullmatch(wert):
            gefunden.add(wert)
        else:
            raise Abgelehnt(403, f"Platzhalter im {wo} nur als ganzer Wert eines Anmelde-Parameters "
                                 f"(key, token, client_secret …), nicht in „{name[:40]}“")
    return gefunden


def ist_formular(inhaltsarten: list[str], koerper: str) -> bool:
    """Genau EIN Content-Type, genau Formular, und nichts, was ein Ziel auch als JSON lesen koennte."""
    if len(inhaltsarten) != 1:
        return False
    art = inhaltsarten[0].split(";", 1)[0].strip().lower()
    return art == FORMULAR and koerper.lstrip()[:1] not in ("{", "[")


def parameter_ersetzen(abfrage: str, werte: dict[str, str]) -> str:
    """Nur den Wert eines geprueften Anmelde-Parameters tauschen und neu kodieren —
    keine Textersetzung ueber das Ganze (anders gelesene Teile bleiben unberuehrt)."""
    paare = urllib.parse.parse_qsl(abfrage, keep_blank_values=True)
    neu = [(k, werte.get(v, v) if ANMELDE_PARAMETER.match(k) else v) for k, v in paare]
    return urllib.parse.urlencode(neu, quote_via=urllib.parse.quote)


def pfad_ersetzen(pfad: str, werte: dict[str, str]) -> str:
    ort, sep, abfrage = pfad.partition("?")
    if not sep or not platzhalter_in(abfrage):
        return pfad
    return f"{ort}?{parameter_ersetzen(abfrage, werte)}"


def platzhalter_ausserhalb_der_koepfe(pfad: str, inhaltsart, koerper: str | None) -> set[str]:
    """Platzhalter in Adresse und Koerper — nur an Anmeldestellen, sonst ``Abgelehnt``.

    In Kopfzeilen darf ein Platzhalter ueberall stehen (dort stehen Keys, und kaum ein
    Dienst speichert fremde Kopfzeilen). Im Pfad, in einem JSON-Koerper oder in einem
    beliebigen Parameter dagegen koennte ein Agent den echten Wert beim Ziel
    ablegen lassen — als Dateiname, Gist-Inhalt, Kommentar — und ihn spaeter lesen.
    """
    ort, _, abfrage = pfad.partition("?")
    if platzhalter_in(urllib.parse.unquote(ort)):
        raise Abgelehnt(403, "Platzhalter im Pfad der Adresse werden nicht ersetzt")
    gefunden = _parameter_platzhalter(abfrage, "Adressteil")
    if koerper and platzhalter_in(koerper):
        arten = [inhaltsart] if isinstance(inhaltsart, str) else list(inhaltsart or [])
        if ist_formular(arten, koerper):
            gefunden |= _parameter_platzhalter(koerper, "Formular")
        else:
            raise Abgelehnt(403, "Platzhalter im Inhalt einer Anfrage werden nicht ersetzt — "
                                 "nur in Kopfzeilen und Anmelde-Parametern")
    return gefunden


def ersetzen(text: str, werte: dict[str, str]) -> str:
    for p, w in werte.items():
        text = text.replace(p, w)
    return text


def kopf_ersetzen(name: str, wert: str, werte: dict[str, str]) -> str:
    if name.lower() == "authorization":
        innen = _basic_entpacken(wert)
        if innen is not None and platzhalter_in(innen):
            return "Basic " + base64.b64encode(ersetzen(innen, werte).encode()).decode()
    return ersetzen(wert, werte)


def _varianten(wert: str) -> set[str]:
    """Der Wert so, wie ein Ziel ihn zurueckgeben koennte: roh, JSON-, URL-, HTML-kodiert, base64."""
    import html
    v = {wert, wert.replace("/", "\\/"), urllib.parse.quote(wert, safe=""), urllib.parse.quote_plus(wert),
         html.escape(wert), base64.b64encode(wert.encode()).decode(),
         base64.urlsafe_b64encode(wert.encode()).decode().rstrip("=")}
    return {x for x in v if len(x) >= 8}


def zurueck_maskieren(text: str, werte: dict[str, str]) -> str:
    """Spiegelt das Ziel einen echten Wert zurueck, sieht der Agent wieder nur den Platzhalter."""
    for p, w in werte.items():
        for v in sorted(_varianten(w or ""), key=len, reverse=True):
            text = text.replace(v, p)
    return text


def zurueck_maskieren_bytes(daten: bytes, werte: dict[str, str]) -> bytes:
    """Wie ``zurueck_maskieren``, fuer beliebige Inhalte (auch binaer)."""
    for p, w in werte.items():
        for v in sorted(_varianten(w or ""), key=len, reverse=True):
            daten = daten.replace(v.encode(), p.encode())
    return daten


#: Groesste Antwort, die der Proxy zum Verdecken puffert/entpackt. Darueber bricht er ab,
#: statt eine ungepruefte Antwort durchzulassen.
MAX_ANTWORT = 32 * 1024 * 1024


def host_werte_passen(werte: list[str], ziel: str) -> bool:
    """Alle Angaben, nach denen ein Ziel routet (jeder Host-Kopf, :authority), muessen das
    Tunnelziel nennen — und mindestens eine muss da sein. Sonst Domain-Fronting."""
    gesehen = False
    for w in werte:
        w = (w or "").strip().lower()
        if not w:
            continue
        gesehen = True
        if w.startswith("["):
            host = w[1:w.find("]")] if "]" in w else w
        else:
            host = w.rsplit(":", 1)[0] if w.count(":") == 1 else w
        if host.rstrip(".") != ziel:
            return False
    return gesehen


def entpacken_begrenzt(daten: bytes, kodierung: str, grenze: int = MAX_ANTWORT) -> bytes:
    """Antwort entpacken, aber nie ueber ``grenze`` hinaus (Kompressionsbombe) — oder ``Abgelehnt``."""
    import zlib
    k = (kodierung or "identity").strip().lower()
    if k in ("", "identity"):
        return daten
    if k in ("gzip", "x-gzip", "deflate"):
        wbits = 16 + zlib.MAX_WBITS if k != "deflate" else zlib.MAX_WBITS
        d = zlib.decompressobj(wbits)
        try:
            ergebnis = d.decompress(daten, grenze + 1)
        except zlib.error:
            if k != "deflate":
                raise Abgelehnt(502, "Antwort nicht lesbar")
            d = zlib.decompressobj(-zlib.MAX_WBITS)           # rohes deflate
            ergebnis = d.decompress(daten, grenze + 1)
        if len(ergebnis) > grenze or d.unconsumed_tail:
            raise Abgelehnt(502, "Antwort zu gross zum Prüfen")
        return ergebnis
    raise Abgelehnt(502, f"Kodierung {k} wird nicht geprüft")


class Maske:
    """Alle Werte, die der Proxy je fuer einen Agenten eingesetzt hat — verdeckt in JEDER
    Antwort an ihn. Sonst liesse ein Agent den Wert beim Ziel ablegen und holte ihn mit
    einem zweiten Abruf ohne Platzhalter wieder ab."""

    def __init__(self, jetzt=time.monotonic):
        self.jetzt = jetzt
        self._werte: dict[str, dict[str, tuple[str, float]]] = {}

    def merken(self, agent_id: str, werte: dict[str, str]) -> None:
        t = self.jetzt()
        eintraege = self._werte.setdefault(agent_id, {})
        for p, w in werte.items():
            eintraege[p] = (w, t)

    def werte(self, agent_id: str) -> dict[str, str]:
        t = self.jetzt()
        eintraege = self._werte.get(agent_id, {})
        for p in [p for p, (_, zeit) in eintraege.items() if t - zeit >= MASKE_DAUER]:
            eintraege.pop(p, None)
        return {p: w for p, (w, _) in eintraege.items()}


class Orchestrator:
    """Fragt den Orchestrator — mit Dienst-Schluessel, kurz zwischengespeichert."""

    def __init__(self, url: str, token: str, jetzt=time.monotonic, senden=None):
        self.url = url.rstrip("/")
        self.token = token
        self.jetzt = jetzt
        self._senden = senden or self._http
        self._anmeldungen: dict[tuple[str, str], tuple[float, list[str]]] = {}
        self._werte: dict[tuple, tuple[float, dict[str, str]]] = {}

    def _http(self, pfad: str, daten: dict) -> tuple[int, dict]:
        anfrage = urllib.request.Request(
            self.url + pfad, data=json.dumps(daten).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        # Direkt, nie ueber einen Proxy aus der Umgebung.
        oeffner = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with oeffner.open(anfrage, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except Exception:  # noqa: BLE001
                return e.code, {}
        except Exception as e:  # noqa: BLE001
            raise OrchestratorFehler(str(e)) from e

    def hosts(self, agent: tuple[str, str]) -> list[str] | None:
        """Ziele, fuer die TLS aufgebrochen wird. None = Anmeldung falsch."""
        jetzt = self.jetzt()
        gemerkt = self._anmeldungen.get(agent)
        if gemerkt and jetzt - gemerkt[0] < ANMELDUNG_FRISCH:
            return gemerkt[1]
        try:
            status, antwort = self._senden("/api/v1/egress/verbindung",
                                           {"agent_id": agent[0], "agent_token": agent[1]})
        except OrchestratorFehler:
            if gemerkt and jetzt - gemerkt[0] < ANMELDUNG_NOTFALL:
                return gemerkt[1]
            raise
        if status == 200:
            hosts = [str(h) for h in antwort.get("hosts") or []]
            self._anmeldungen[agent] = (jetzt, hosts)
            return hosts
        if status in (401, 422):
            self._anmeldungen.pop(agent, None)
            return None
        # Nur ein Ausfall (5xx) ueberbrueckt — eine Ablehnung (etwa falscher
        # Dienst-Schluessel) nie.
        if status >= 500 and gemerkt and jetzt - gemerkt[0] < ANMELDUNG_NOTFALL:
            return gemerkt[1]
        raise OrchestratorFehler(f"Antwort {status}")

    def werte(self, agent: tuple[str, str], host: str, platzhalter: set[str]) -> dict[str, str]:
        """Echte Werte — alle oder ``Abgelehnt``."""
        schluessel = (agent, host.lower(), tuple(sorted(platzhalter)))
        jetzt = self.jetzt()
        gemerkt = self._werte.get(schluessel)
        if gemerkt and jetzt - gemerkt[0] < WERTE_FRISCH:
            return gemerkt[1]
        try:
            status, antwort = self._senden("/api/v1/egress/aufloesen", {
                "agent_id": agent[0], "agent_token": agent[1], "host": host,
                "platzhalter": sorted(platzhalter),
            })
        except OrchestratorFehler:
            if gemerkt and jetzt - gemerkt[0] < WERTE_NOTFALL:
                return gemerkt[1]
            raise
        if status == 200:
            werte = {str(k): str(v) for k, v in (antwort.get("werte") or {}).items()}
            if set(werte) != platzhalter:
                raise Abgelehnt(502, "Unvollständige Antwort des Orchestrators")
            # Abgelaufene Eintraege nicht ewig im Speicher halten
            for k in [k for k, (t, _) in self._werte.items() if jetzt - t >= WERTE_NOTFALL]:
                self._werte.pop(k, None)
            self._werte[schluessel] = (jetzt, werte)
            return werte
        self._werte.pop(schluessel, None)
        if status == 403:
            raise Abgelehnt(403, "Key für dieses Ziel nicht freigegeben")
        raise Abgelehnt(502, f"Orchestrator antwortet {status}")
