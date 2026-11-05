"""AWS-Bedrock-Provider — Runtime-Converse-API (``converse_stream``) ueber boto3.

Kein OpenAI-Kompatibilitaets-Trick: Converse ist die einheitliche Schnittstelle
von Bedrock fuer alle Modellfamilien, und sie ist strenger als die Messages-API.
Darum steckt hier mehr drin als im AnthropicProvider:

* Rollen muessen sich abwechseln und das Gespraech beginnt mit ``user`` — mehrere
  Werkzeug-Ergebnisse eines Zugs landen in EINER Nachricht.
* Leere Textbloecke sind ein Fehler, sie fallen weg.
* Stehen ``toolUse``/``toolResult`` im Verlauf, MUSS ``toolConfig`` mitgehen. Ein
  Zug ohne Werkzeuge (der Abschluss nach dem Zugbudget) bekommt den Verlauf
  deshalb als Text.

Anmeldung, je nach Konto:

* **Bedrock-API-Schluessel** (Bearer): ``LLM_API_KEY``. Geht auch durch den
  Egress-Proxy — der Agent haelt dort nur den Platzhalter.
* **IAM** (``LLM_AWS_ACCESS_KEY_ID`` + Secret in ``LLM_API_KEY``, SigV4): signiert
  wird HIER im Agenten, ein Platzhalter kann das nicht. Hinter dem Proxy kommt das
  Secret deshalb gar nicht erst an (``LLM_BEDROCK_IAM_GESPERRT``,
  orchestrator/app/core/egress.py) und der Provider sagt, woran es liegt.

boto3 ist synchron. Es laeuft in einem eigenen Daemon-Thread, die Ereignisse kommen
ueber eine Queue zurueck — ein haengender Lesevorgang blockiert so weder die
Ereignisschleife noch den Standard-Thread-Pool.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
import time
from typing import AsyncIterator, Callable

from botocore.exceptions import (
    ClientError,
    ConnectionError as BotoVerbindungsfehler,
    ConnectTimeoutError,
    HTTPClientError,
    ParamValidationError,
    ReadTimeoutError,
    ResponseStreamingError,
)

from app import multimodal
from app.providers.base import (
    BaseLLMProvider,
    ChatMessage,
    LLMEvent,
    describe_failure,
    format_exception,
)

logger = logging.getLogger(__name__)

_REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d{1,2}$")
_ENDPOINT_RE = re.compile(r"(?:^|//)bedrock-runtime(?:-fips)?\.([a-z0-9-]+)\.amazonaws\.com(?:[:/]|$)")
#: Mehr erlaubt Converse fuer einen Werkzeugnamen nicht (ToolName im Service-Modell);
#: schon ein Verstoss laesst die ganze Anfrage scheitern.
_WERKZEUGNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_BILDFORMATE = {"png", "jpeg", "gif", "webp"}

#: Wortlaute von Bedrock fuer „dieses Modell kann keine Werkzeuge“ — belegt ist
#: ``This model doesn't support tool use.`` und ``… in streaming mode.``; die
#: Varianten decken Umformulierungen ab, ohne jede beliebige Ablehnung zu treffen.
_OHNE_WERKZEUGE_RE = re.compile(
    r"(?:does ?n[o']?t|do not|not|no) support\w*\b.{0,40}\btool"
    r"|\btool(?:s| use| calling|Config)?\b.{0,40}\b(?:not supported|unsupported)",
    re.IGNORECASE,
)

#: Kapazitaet und Stoerung: Code → Status, wie ``model_fallback`` ihn erkennt. Hier
#: lohnt der Wechsel auf ein Ausweichmodell. AWS meldet einige davon mit anderem
#: Status (ServiceQuotaExceeded = 400, ModelStreamError = 424) — massgeblich ist
#: die Bedeutung, nicht die Zahl.
_KAPAZITAET: dict[str, int] = {
    "ThrottlingException": 429,
    "TooManyRequestsException": 429,
    "ServiceQuotaExceededException": 429,
    "ModelNotReadyException": 429,
    "ModelTimeoutException": 504,
    "ModelErrorException": 502,
    "ModelStreamErrorException": 502,
    "InternalServerException": 500,
    "ServiceUnavailableException": 503,
}

#: Anmeldung oder Berechtigung. Egal mit welchem Status: nie ausweichen, ein anderes
#: Modell scheitert genauso.
_ZUGANG = {
    "AccessDeniedException",
    "UnrecognizedClientException",
    "ExpiredTokenException",
    "ExpiredToken",
    "InvalidSignatureException",
    "IncompleteSignatureException",
    "InvalidClientTokenId",
    "SignatureDoesNotMatch",
    "UnauthorizedException",
}

#: So stehen Fehler MITTEN im Strom, wenn sie nicht als Ausnahme kommen.
_STROM_FEHLER = {
    "internalServerException": "InternalServerException",
    "modelStreamErrorException": "ModelStreamErrorException",
    "validationException": "ValidationException",
    "throttlingException": "ThrottlingException",
    "serviceUnavailableException": "ServiceUnavailableException",
}

_LEERES_SCHEMA = {"type": "object", "properties": {}}
_ENDE = object()


def region_aufloesen(region: str, endpoint: str) -> str:
    """Region aus der Angabe, sonst aus dem Endpunkt — sonst leer.

    Bewusst KEIN Rueckfall auf us-east-1: Wohin die Daten gehen, entscheidet der
    Betreiber. Ein stiller Standard schickte sie womoeglich aus der EU heraus.
    """
    roh = (region or "").strip().lower()
    if roh:
        return roh if _REGION_RE.match(roh) else ""
    m = _ENDPOINT_RE.search((endpoint or "").lower())
    return m.group(1) if m and _REGION_RE.match(m.group(1)) else ""


def _bild_block(bild: dict) -> dict | None:
    """Generischer Bildblock → Converse. ``None``, wenn er nicht darstellbar ist."""
    art = str(bild.get("media_type") or "image/jpeg").split("/")[-1].lower()
    art = "jpeg" if art == "jpg" else art
    if art not in _BILDFORMATE:
        return None
    try:
        return {"image": {"format": art, "source": {"bytes": base64.b64decode(bild.get("data") or "", validate=True)}}}
    except (ValueError, TypeError):
        return None


def _bloecke(inhalt: object) -> list[dict]:
    """Text und Bilder einer Nachricht. Leerer Text ist bei Converse ein Fehler."""
    text, bilder = multimodal.split_blocks(inhalt)
    bloecke: list[dict] = [{"text": text}] if text.strip() else []
    for bild in bilder:
        block = _bild_block(bild)
        bloecke.append(block if block else {"text": "[Bild nicht darstellbar]"})
    return bloecke


def _argumente(tc: dict) -> dict:
    args = (tc.get("function") or {}).get("arguments")
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args:
        try:
            wert = json.loads(args)
        except json.JSONDecodeError:
            return {}
        return wert if isinstance(wert, dict) else {}
    return {}


def _anhaengen(ausgabe: list[dict], rolle: str, bloecke: list[dict]) -> None:
    """Gleiche Rolle hintereinander wird EINE Nachricht — Converse besteht darauf."""
    if not bloecke:
        return
    if ausgabe and ausgabe[-1]["role"] == rolle:
        ausgabe[-1]["content"].extend(bloecke)
    else:
        ausgabe.append({"role": rolle, "content": list(bloecke)})


def nachrichten_uebersetzen(messages: list[ChatMessage], mit_werkzeugen: bool) -> tuple[str, list[dict]]:
    """ChatMessage-Verlauf → (Systemtext, Converse-Nachrichten).

    ``mit_werkzeugen=False``: kein ``toolConfig`` in der Anfrage. Dann sind
    ``toolUse``/``toolResult`` im Verlauf verboten und werden zu Text — der Inhalt
    bleibt fuer das Modell lesbar, nur ohne den Vertrag dahinter.
    """
    system: list[str] = []
    ausgabe: list[dict] = []
    for msg in messages:
        if msg.role == "system":
            text, _ = multimodal.split_blocks(msg.content)
            if text.strip():
                system.append(text)
        elif msg.role == "tool":
            text, bilder = multimodal.split_blocks(msg.content)
            if mit_werkzeugen:
                inhalt: list[dict] = [{"text": text if text.strip() else "(keine Ausgabe)"}]
                inhalt.extend(b for b in map(_bild_block, bilder) if b)
                _anhaengen(ausgabe, "user", [{"toolResult": {"toolUseId": msg.tool_call_id, "content": inhalt}}])
            else:
                kopf = f"[Ergebnis von {msg.name or 'Werkzeug'}]"
                bloecke = [{"text": f"{kopf} {text}".rstrip()}]
                bloecke.extend(b for b in map(_bild_block, bilder) if b)
                _anhaengen(ausgabe, "user", bloecke)
        elif msg.role == "assistant":
            bloecke = _bloecke(msg.content)
            for tc in msg.tool_calls or []:
                name = (tc.get("function") or {}).get("name", "")
                if mit_werkzeugen:
                    bloecke.append({"toolUse": {"toolUseId": tc.get("id", ""), "name": name, "input": _argumente(tc)}})
                else:
                    bloecke.append({"text": f"[Aufruf von {name}: {json.dumps(_argumente(tc), ensure_ascii=False)}]"})
            _anhaengen(ausgabe, "assistant", bloecke)
        else:
            _anhaengen(ausgabe, "user", _bloecke(msg.content))
    if not ausgabe or ausgabe[0]["role"] != "user":
        # Ein Verdichten des Verlaufs kann vorn eine Assistenten-Nachricht stehen lassen.
        ausgabe.insert(0, {"role": "user", "content": [{"text": "(Fortsetzung des Gesprächs)"}]})
    return "\n\n".join(system), ausgabe


def werkzeuge_uebersetzen(tools: list[dict] | None) -> list[dict]:
    """OpenAI-Werkzeuge → ``toolConfig.tools``.

    Ein leerer oder doppelter Name liesse die ganze Anfrage scheitern; wie beim
    AnthropicProvider gewinnt der erste. Das ist kein stilles Weglassen der
    Werkzeuge: Es faellt nur der einzelne unbrauchbare Eintrag, und zwar laut.
    """
    ausgabe: list[dict] = []
    gesehen: set[str] = set()
    for t in tools or []:
        func = t.get("function") or {}
        name = func.get("name") or ""
        if name in gesehen:
            continue
        if not _WERKZEUGNAME_RE.match(name):
            logger.warning("[Bedrock] Werkzeug %r weggelassen: Name passt nicht zu Converse", name[:80])
            continue
        gesehen.add(name)
        schema = func.get("parameters")
        ausgabe.append({"toolSpec": {
            "name": name,
            # Pflicht bei Converse: mindestens ein Zeichen.
            "description": func.get("description") or name,
            "inputSchema": {"json": schema if isinstance(schema, dict) and schema else dict(_LEERES_SCHEMA)},
        }})
    return ausgabe


def fehler_aus_antwort(code: str, nachricht: str, status: int, modell: str, mit_werkzeugen: bool) -> LLMEvent:
    """Fehler von Bedrock → Ereignis, das die Runner richtig einordnen.

    Die Einordnung haengt am Wortlaut (``model_fallback``), die Anzeige am Praefix
    ``API error NNN:`` (``nutzertext_fuer_api_fehler``). Deshalb bewusst OHNE Groessen
    und Zeiten im Text: ``Nachrichten=500`` oder ``nach 401.2s`` wuerde sonst als
    Status gelesen — die Umstaende gehen ins Protokoll.

    Ablehnungen der Anfrage (400) gehen ohne dieses Praefix hinaus: Der Nutzer sieht
    sonst nur „Das Sprachmodell hat die Anfrage abgelehnt“ — und gerade hier steht
    im Originaltext, was zu tun ist.
    """
    kurz = f"{code}: {nachricht}".strip().rstrip(":")
    klein = (nachricht or "").lower()
    if code in _KAPAZITAET:
        return LLMEvent(type="error", text=f"API error {_KAPAZITAET[code]}: {kurz} (Modell {modell})")
    if code in _ZUGANG:
        return LLMEvent(type="error", text=f"API error 403: {kurz} (Modell {modell})", konfigurationsfehler=True)
    if code == "ValidationException" or status == 400:
        if mit_werkzeugen and _OHNE_WERKZEUGE_RE.search(nachricht or ""):
            art = (" im Streaming-Betrieb" if "stream" in klein else "")
            return LLMEvent(
                type="error", konfigurationsfehler=True,
                text=f"Das Modell {modell} unterstützt bei AWS Bedrock{art} keine Werkzeug-Aufrufe "
                     f"(Grund laut AWS: {nachricht}). Dieser Agent braucht Werkzeuge: Bitte ein Modell "
                     "mit Tool-Unterstützung wählen oder die Werkzeuge in der LLM-Konfiguration abschalten.",
            )
        if "on-demand throughput" in klein or "inference profile" in klein:
            return LLMEvent(
                type="error", konfigurationsfehler=True,
                text=f"Das Modell {modell} lässt sich bei AWS Bedrock in dieser Region nicht direkt "
                     "aufrufen, es braucht ein Inferenzprofil: Bitte die Profil-Kennung eintragen, "
                     f"z. B. mit Vorsatz eu. oder us. (Grund laut AWS: {nachricht})",
            )
        return LLMEvent(type="error", konfigurationsfehler=True,
                        text=f"AWS Bedrock hat die Anfrage für Modell {modell} abgelehnt ({kurz}).")
    return LLMEvent(type="error", text=f"API error {status or 500}: {kurz} (Modell {modell})",
                    konfigurationsfehler=400 <= status < 500 and status not in (408, 429))


class StromAuswertung:
    """Zustand eines Antwortstroms: Bedrock-Ereignisse → normalisierte ``LLMEvent``.

    Ohne Netz und ohne Threads, damit die heiklen Faelle einzeln pruefbar sind.
    """

    def __init__(self, modell: str, mit_werkzeugen: bool):
        self.modell = modell
        self.mit_werkzeugen = mit_werkzeugen
        self.werkzeuge: dict[int, dict] = {}
        self.stop_grund: str | None = None
        self.nutzung: dict = {}
        self.ausgabe_gesehen = False
        self.fehlgeschlagen = False

    def _werkzeug_fertig(self, puffer: dict) -> LLMEvent:
        roh = puffer["json"]
        try:
            eingabe = json.loads(roh) if roh else {}
        except json.JSONDecodeError:
            eingabe = None
        if not isinstance(eingabe, dict):
            # Wie der AnthropicProvider: das Werkzeug meldet den Fehler dem Modell,
            # das korrigieren kann. Ein stilles {} sieht wie ein gueltiger Aufruf aus.
            eingabe = {"raw": roh}
        return LLMEvent(type="tool_call", tool_id=puffer["id"], tool_name=puffer["name"], tool_input=eingabe)

    def ereignis(self, ereignis: dict) -> list[LLMEvent]:
        for schluessel, code in _STROM_FEHLER.items():
            if schluessel in ereignis:
                self.fehlgeschlagen = True
                nachricht = str((ereignis[schluessel] or {}).get("message") or "")
                return [fehler_aus_antwort(code, nachricht, 0, self.modell, self.mit_werkzeugen)]

        if "contentBlockStart" in ereignis:
            teil = ereignis["contentBlockStart"]
            start = (teil.get("start") or {}).get("toolUse")
            if start:
                self.werkzeuge[teil.get("contentBlockIndex", 0)] = {
                    "id": start.get("toolUseId", ""), "name": start.get("name", ""), "json": "",
                }
        elif "contentBlockDelta" in ereignis:
            teil = ereignis["contentBlockDelta"]
            delta = teil.get("delta") or {}
            if delta.get("text"):
                self.ausgabe_gesehen = True
                return [LLMEvent(type="text_delta", text=delta["text"])]
            if "toolUse" in delta:
                puffer = self.werkzeuge.get(teil.get("contentBlockIndex", 0))
                if puffer is not None:
                    puffer["json"] += (delta["toolUse"] or {}).get("input") or ""
        elif "contentBlockStop" in ereignis:
            puffer = self.werkzeuge.pop(ereignis["contentBlockStop"].get("contentBlockIndex", 0), None)
            if puffer is not None:
                self.ausgabe_gesehen = True
                return [self._werkzeug_fertig(puffer)]
        elif "messageStop" in ereignis:
            self.stop_grund = ereignis["messageStop"].get("stopReason") or ""
        elif "metadata" in ereignis:
            self.nutzung = ereignis["metadata"].get("usage") or {}
        return []

    def abschluss(self) -> list[LLMEvent]:
        """Nach dem letzten Ereignis. Ein Strom ohne ``messageStop`` ist NICHT fertig.

        Liefe das als „done“, stuende eine halbe Antwort als Ergebnis da, und niemand
        erführe davon. Fehlt nur ``metadata`` (kommt nach ``messageStop``), ist die
        Antwort vollstaendig — dann fehlt lediglich die Token-Zaehlung.
        """
        if self.fehlgeschlagen:
            return []
        if self.stop_grund is None:
            return [LLMEvent(type="error", text=f"Read error: Antwortstrom von Modell {self.modell} "
                                                 "brach vor dem Ende ab (incomplete read)")]
        ereignisse = [self._werkzeug_fertig(p) for _, p in sorted(self.werkzeuge.items())]
        self.werkzeuge.clear()
        if ereignisse:
            self.ausgabe_gesehen = True
        if self.stop_grund in ("content_filtered", "guardrail_intervened") and not self.ausgabe_gesehen:
            return [LLMEvent(type="error", konfigurationsfehler=True,
                             text=f"AWS Bedrock hat die Antwort von Modell {self.modell} blockiert "
                                  f"(content filter: {self.stop_grund}).")]
        return ereignisse + [LLMEvent(
            type="done",
            input_tokens=int(self.nutzung.get("inputTokens") or 0),
            output_tokens=int(self.nutzung.get("outputTokens") or 0),
            cached_tokens=int(self.nutzung.get("cacheReadInputTokens") or 0),
            cache_write_tokens=int(self.nutzung.get("cacheWriteInputTokens") or 0),
        )]


class _Lauf:
    """Eine laufende Anfrage — von aussen abbrechbar, aus jedem Thread."""

    def __init__(self, senden: Callable[[object], None]):
        self.senden = senden
        self.abgebrochen = False
        self._stream = None
        self._sperre = threading.Lock()

    def strom_setzen(self, stream) -> None:
        with self._sperre:
            self._stream = stream
            abgebrochen = self.abgebrochen
        if abgebrochen:
            self._strom_schliessen(stream)

    def abbrechen(self) -> None:
        with self._sperre:
            self.abgebrochen = True
            stream, self._stream = self._stream, None
        if stream is not None:
            self._strom_schliessen(stream)

    @staticmethod
    def _strom_schliessen(stream) -> None:
        try:
            stream.close()
        except Exception:  # noqa: BLE001 — der Lesevorgang ist ohnehin zu Ende
            logger.debug("[Bedrock] Strom liess sich nicht schliessen", exc_info=True)


class BedrockProvider(BaseLLMProvider):
    """Provider fuer AWS Bedrock ueber ``converse_stream``."""

    def __init__(
        self,
        api_endpoint: str,
        api_key: str,
        model_name: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        aws_region: str = "",
        aws_access_key_id: str = "",
        aws_session_token: str = "",
        iam_gesperrt: bool = False,
        client_fabrik: Callable[[], object] | None = None,
        **kwargs,
    ):
        super().__init__(api_endpoint or "", api_key, model_name, max_tokens, temperature, **kwargs)
        self.aws_region = region_aufloesen(aws_region, self.api_endpoint)
        self.aws_region_roh = (aws_region or "").strip()
        self.aws_access_key_id = (aws_access_key_id or "").strip()
        self.aws_session_token = (aws_session_token or "").strip()
        self.iam_gesperrt = iam_gesperrt
        self._client_fabrik = client_fabrik or self._client_bauen
        self._client = None
        self._client_sperre = threading.Lock()
        self._laeufe: set[_Lauf] = set()

    def _einrichtungsfehler(self) -> str | None:
        """Was fehlt, bevor ueberhaupt etwas ans Netz geht."""
        if self.iam_gesperrt:
            return ("AWS Bedrock: IAM-Zugangsschlüssel funktionieren hinter dem Egress-Proxy nicht, "
                    "weil der Agent die Anfrage selbst signieren müsste und dafür das Secret bräuchte. "
                    "Bitte im KI-Konto einen Bedrock-API-Schlüssel eintragen und das Feld "
                    "„AWS Access Key ID“ leeren.")
        if not self.model_name:
            return "AWS Bedrock: Es ist kein Modell eingetragen."
        if not self.aws_region:
            if self.aws_region_roh:
                return f"AWS Bedrock: „{self.aws_region_roh[:40]}“ ist keine gültige AWS-Region."
            return "AWS Bedrock: Es ist keine Region eingetragen (z. B. eu-central-1)."
        if not self.api_key:
            return ("AWS Bedrock: Zugangsdaten fehlen. Bitte im KI-Konto einen Bedrock-API-Schlüssel "
                    "oder Access Key ID und Secret Access Key eintragen.")
        return None

    def _client_bauen(self):
        import boto3
        import botocore.session
        from botocore.config import Config
        from botocore.tokens import FrozenAuthToken

        einstellungen = {
            "region_name": self.aws_region,
            "connect_timeout": 10,
            "read_timeout": 300,
            # Nur vor dem ersten Byte; Ausweichmodelle regelt model_fallback.
            "retries": {"mode": "standard", "total_max_attempts": 3},
        }
        kern = botocore.session.get_session()
        if self.aws_access_key_id:
            sitzung = boto3.session.Session(
                botocore_session=kern,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.api_key,
                aws_session_token=self.aws_session_token or None,
            )
        else:
            # Der Schluessel gehoert in diesen Client, nicht in die Umgebung
            # (AWS_BEARER_TOKEN_BEDROCK): die erben sonst alle Kindprozesse.
            schluessel = self.api_key

            class _FesterSchluessel:
                def load_token(self, **_kwargs):
                    return FrozenAuthToken(schluessel)

            kern.register_component("token_provider", _FesterSchluessel())
            # Sonst sucht botocore trotzdem nach Credentials: Metadatendienst des
            # Hosts, 2 s Wartezeit. Der Bearer-Signierer liest sie nie.
            kern.set_credentials("unbenutzt", "unbenutzt")
            sitzung = boto3.session.Session(botocore_session=kern)
            einstellungen["signature_version"] = "bearer"
        return sitzung.client(
            "bedrock-runtime",
            endpoint_url=self.api_endpoint or None,
            config=Config(**einstellungen),
        )

    def _client_holen(self):
        with self._client_sperre:
            if self._client is None:
                self._client = self._client_fabrik()
            return self._client

    def _anfrage_bauen(self, messages: list[ChatMessage], tools: list[dict] | None) -> dict:
        werkzeuge = werkzeuge_uebersetzen(tools)
        system, nachrichten = nachrichten_uebersetzen(messages, bool(werkzeuge))
        anfrage: dict = {"modelId": self.model_name, "messages": nachrichten}
        if system:
            anfrage["system"] = [{"text": system}]
        einstellungen: dict = {}
        # 0 = keine eigene Grenze; ohne Angabe gilt das Maximum des Modells.
        if self.max_tokens:
            einstellungen["maxTokens"] = self.max_tokens
        # Wie der AnthropicProvider ohne Temperatur fuer Claude: neuere Claude-Modelle
        # weisen sie ab. Sonst gilt bei Converse der Bereich 0..1 (die Oberflaeche
        # erlaubt bis 2).
        if "claude" not in self.model_name.lower():
            einstellungen["temperature"] = max(0.0, min(float(self.temperature), 1.0))
        if einstellungen:
            anfrage["inferenceConfig"] = einstellungen
        if werkzeuge:
            anfrage["toolConfig"] = {"tools": werkzeuge}
        return anfrage

    def _fehler_ereignis(self, e: Exception, anfrage: dict, gestartet: float) -> LLMEvent:
        """Ausnahme → Ereignis. Wortlaute passend zu ``model_fallback``."""
        logger.warning("[Bedrock] %s", describe_failure(
            e, messages=anfrage["messages"], model=self.model_name, started=gestartet,
        ))
        modell = self.model_name
        if isinstance(e, ClientError):
            fehler = e.response.get("Error") or {}
            status = int((e.response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
            code = str(fehler.get("Code") or "")
            # Fehler im Strom tragen den Elementnamen (throttlingException), nicht den Typ.
            code = _STROM_FEHLER.get(code, code)
            # Antworten ohne AWS-Rumpf (Egress-Proxy, Sperre davor) tragen die Zahl als Code.
            if code.isdigit():
                status = int(code)
            return fehler_aus_antwort(code, str(fehler.get("Message") or ""), status, modell, "toolConfig" in anfrage)
        if isinstance(e, ParamValidationError):
            # Der Text zitiert Teile der Anfrage — kurz halten, der Rest steht im Protokoll.
            return LLMEvent(type="error", konfigurationsfehler=True,
                            text=f"AWS Bedrock: Anfrage für Modell {modell} ist ungültig: {str(e)[:300]}")
        if isinstance(e, (ConnectTimeoutError, ReadTimeoutError)):
            return LLMEvent(type="error", text=f"Request timed out: {format_exception(e)}")
        if isinstance(e, ResponseStreamingError):
            return LLMEvent(type="error", text=f"Read error: {format_exception(e)}")
        if isinstance(e, (HTTPClientError, BotoVerbindungsfehler)):
            return LLMEvent(type="error", text=f"Connection error: {format_exception(e)}")
        return LLMEvent(type="error", text=f"Unexpected error: {format_exception(e)}")

    def _arbeiten(self, anfrage: dict, lauf: _Lauf) -> None:
        """Laeuft im eigenen Thread: Anfrage stellen, Strom lesen, Ereignisse weiterreichen."""
        gestartet = time.monotonic()
        auswertung = StromAuswertung(self.model_name, "toolConfig" in anfrage)
        try:
            antwort = self._client_holen().converse_stream(**anfrage)
            stream = antwort["stream"]
            lauf.strom_setzen(stream)
            for ereignis in stream:
                if lauf.abgebrochen:
                    return
                for ausgabe in auswertung.ereignis(ereignis):
                    lauf.senden(ausgabe)
                if auswertung.fehlgeschlagen:
                    break
            if not lauf.abgebrochen:
                for ausgabe in auswertung.abschluss():
                    lauf.senden(ausgabe)
        except Exception as e:  # noqa: BLE001 — jeder Fehler wird zum Ereignis, nie zum toten Thread
            if not lauf.abgebrochen:
                lauf.senden(self._fehler_ereignis(e, anfrage, gestartet))
        finally:
            lauf.abbrechen()
            lauf.senden(_ENDE)

    async def _stream_completion_impl(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        fehler = self._einrichtungsfehler()
        if fehler:
            yield LLMEvent(type="error", text=fehler, konfigurationsfehler=True)
            return

        schleife = asyncio.get_running_loop()
        warteschlange: asyncio.Queue = asyncio.Queue()

        def senden(wert: object) -> None:
            try:
                schleife.call_soon_threadsafe(warteschlange.put_nowait, wert)
            except RuntimeError:
                pass  # Schleife schon zu: niemand wartet mehr auf dieses Ereignis

        lauf = _Lauf(senden)
        self._laeufe.add(lauf)
        anfrage = self._anfrage_bauen(messages, tools)
        threading.Thread(target=self._arbeiten, args=(anfrage, lauf), daemon=True,
                         name="bedrock-stream").start()
        try:
            while True:
                wert = await warteschlange.get()
                if wert is _ENDE:
                    return
                yield wert
        finally:
            # Auch wenn der Aufrufer mittendrin aussteigt: Der Strom muss zu.
            self._laeufe.discard(lauf)
            lauf.abbrechen()

    async def close(self) -> None:
        """Unterbricht laufende Anfragen — wie das Schliessen des HTTP-Clients sonst."""
        for lauf in list(self._laeufe):
            lauf.abbrechen()
            lauf.senden(LLMEvent(type="error", text="AWS Bedrock: Anfrage abgebrochen."))
            lauf.senden(_ENDE)
        await super().close()
