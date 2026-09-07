"""„completed" ist eine Behauptung, kein Befund.

Heute wird der Status, den ein Lauf ueber sich selbst MELDET, unveraendert nach
``task:completions`` veroeffentlicht (``task_consumer._run_task``). Ob der Lauf
die Sache auch nur angefasst hat, steht nirgends. Genau daran ist es beim
Kunden am 2026-08-12 gescheitert: zwei Auftraege standen auf „erledigt", im
Ergebnis stand woertlich „nur angekuendigt".

Der Auftrags-Pfad ueber ``LLMRunner`` bekam daraufhin ein Gatter
(``_compliance_gaps``, v1.178.2) — aber das ist ein **Anstupser**, kein Befund:
es fordert den Agenten auf nachzuarbeiten, und wer den Anstupser ignoriert,
endet trotzdem als COMPLETED. Der Claude-Code-Pfad (``AgentRunner``) und der
Codex-Pfad haben ueberhaupt keins.

Dieses Modul trennt deshalb die beiden Dinge, die bisher eins waren (#705):

* **Entscheidung** — was der Lauf ueber sich sagt (``status``).
* **Befund** — was am Lauf beobachtbar ist: welche Werkzeuge WIRKLICH gelaufen
  sind und nicht in einen Fehler liefen.

Das Urteil ersetzt den Status nicht und laesst keine Aufgabe scheitern. Es
haengt sich daneben, damit ein leergelaufener Lauf als solcher erkennbar ist,
statt als Erfolg gebucht zu werden.
"""

from __future__ import annotations

#: Werkzeuge, die zur Buchhaltung eines Laufs gehoeren, nicht zu seiner Sache.
#: Wer nur nachschlaegt, wo er steht, und sich anschliessend selbst bewertet,
#: hat nichts geleistet — im Bericht oben war der einzige Werkzeugaufruf des
#: Laufs die Bewertung selbst.
#:
#: Zwei Namensraeume, weil dieselbe Aufgabe ueber verschiedene Pfade laufen
#: kann: ``LLMRunner`` sieht die eigenen Werkzeugnamen (``save_memory``), der
#: Claude-Code-Pfad die der CLI (``TodoWrite``) und MCP-Namen (``mcp__…``).
BOOKKEEPING_TOOLS = frozenset({
    # LLM-Pfad
    "rate_task", "skill_rate", "skill_search",
    "memory_save", "memory_search", "memory_list", "memory_delete",
    "save_memory", "search_memory", "get_context",
    "brain_search", "brain_related", "brain_get", "brain_list",
    "list_todos", "update_todos", "search_tools", "notify_user",
    "escalate_if_unsure", "request_approval", "check_approval",
    "list_tasks", "list_my_team",
    # Claude-Code-Pfad
    "TodoWrite", "TaskList", "ToolSearch",
})
# ``TaskCreate``/``TaskUpdate`` stehen bewusst NICHT darin: wer Arbeit
# delegiert, hat gearbeitet. Sie hier zu fuehren wuerde ausgerechnet den Lauf
# als leer melden, der richtig verteilt hat.

#: Urteile. Bewusst drei statt zwei: ein Lauf ohne Werkzeuge ist nicht
#: automatisch faul — eine beantwortete Frage braucht keins.
VERIFIED = "verified"
REPORTED = "reported"
UNVERIFIED = "unverified"


def _is_bookkeeping(name: str) -> bool:
    if name in BOOKKEEPING_TOOLS:
        return True
    # MCP-Namen tragen den Werkzeugnamen hinten: mcp__memory__save_memory.
    if name.startswith("mcp__"):
        return name.rsplit("__", 1)[-1] in BOOKKEEPING_TOOLS
    return False


def substantive_tools(succeeded: set[str]) -> set[str]:
    """Die Werkzeuge, die an der SACHE gearbeitet haben."""
    return {name for name in succeeded if not _is_bookkeeping(name)}


def verdict(
    *,
    status: str,
    succeeded: set[str],
    failed: set[str],
    unpaired: set[str] | None = None,
    lightweight: bool = False,
) -> dict:
    """Befund zu einem Lauf — was ist an ihm beobachtbar?

    ``succeeded`` sind die Werkzeuge, deren Ergebnis OHNE Fehler zurueckkam,
    ``failed`` die uebrigen. Die Unterscheidung ist der Kern: ein Lauf, dessen
    einziger Griff nach draussen in einen Fehler lief, hat nichts erreicht —
    auch wenn er hinterher „fertig" sagt. Das ist derselbe Gedanke wie die
    Regel „die Pruefung muss juenger sein als die neueste Arbeit": es zaehlt
    nur, was am Ende noch steht.

    ``lightweight`` markiert die kurzen Laeufe (Frage, Statusabruf). Fuer die
    ist Reden der Normalfall, deshalb bekommen sie kein ``unverified``.
    """
    if status != "completed":
        return {
            "verdict": status,
            "verified": False,
            "reason": f"Lauf endete als {status}",
        }

    echte_arbeit = substantive_tools(succeeded)
    if echte_arbeit:
        return {
            "verdict": VERIFIED,
            "verified": True,
            "reason": f"{len(echte_arbeit)} Werkzeug(e) an der Sache erfolgreich",
            "tools": sorted(echte_arbeit),
        }

    versucht = substantive_tools(failed)
    if versucht:
        return {
            "verdict": UNVERIFIED,
            "verified": False,
            "reason": (
                "meldet fertig, aber jeder Griff an die Sache lief in einen "
                f"Fehler: {', '.join(sorted(versucht))}"
            ),
        }

    # Angefangen, aber nie zurueckgekommen — typisch fuer einen Abbruch mitten
    # im Werkzeug. Das ist kein Fehlschlag, und es als einen zu melden waere
    # eine Behauptung ueber etwas, das wir schlicht nicht gesehen haben.
    offen = substantive_tools(unpaired or set())
    if offen:
        return {
            "verdict": UNVERIFIED,
            "verified": False,
            "reason": (
                "meldet fertig, aber zu keinem Griff an die Sache kam ein "
                f"Ergebnis zurueck: {', '.join(sorted(offen))}"
            ),
        }

    if lightweight:
        return {
            "verdict": REPORTED,
            "verified": False,
            "reason": "kurzer Lauf ohne Werkzeuge — als Antwort plausibel",
        }

    return {
        "verdict": UNVERIFIED,
        "verified": False,
        "reason": (
            "meldet fertig, ohne ein einziges Werkzeug an der Sache — "
            "angekuendigt statt getan"
        ),
    }


class EvidenceLedger:
    """Sammelt waehrend des Laufs mit, was wirklich passiert ist.

    Ein Werkzeug gilt erst als gelaufen, wenn sein Ergebnis ohne Fehler
    zurueckkam. Deshalb wird der Name ueber die ``tool_use_id`` festgehalten
    und erst beim Ergebnis einsortiert. Kommt gar kein Ergebnis — der Lauf
    bricht mitten im Werkzeug ab — bleibt der Aufruf offen und zaehlt NICHT
    als Beleg; das ist die vorsichtige Richtung.
    """

    def __init__(self) -> None:
        self._namen: dict[str, str] = {}
        self._offen_ohne_id: set[str] = set()
        self.succeeded: set[str] = set()
        self.failed: set[str] = set()

    def record_call(self, tool_use_id: str | None, name: str | None) -> None:
        if not name:
            return
        if tool_use_id:
            self._namen[tool_use_id] = name
        else:
            # Ohne Zuordnung koennen wir den Ausgang nie erfahren. Als
            # gescheitert zu buchen waere eine Behauptung, als gelungen
            # Leichtglaeubigkeit — also bleibt er offen. Ein Beleg muss
            # positiv sein, um zu zaehlen.
            self._offen_ohne_id.add(name)

    def record_result(self, tool_use_id: str | None, is_error: bool) -> None:
        name = self._namen.pop(tool_use_id or "", None)
        if not name:
            return
        (self.failed if is_error else self.succeeded).add(name)

    def verdict(self, status: str, lightweight: bool = False) -> dict:
        return verdict(
            status=status,
            succeeded=self.succeeded,
            failed=self.failed,
            unpaired=self._offen_ohne_id | set(self._namen.values()),
            lightweight=lightweight,
        )
