"""„Ich mache das jetzt" — und dann passiert nichts.

Nutzerbericht vom 2026-08-16, per Sprache: „bau mir mal eine kleine
taschenrechner app". Der Agent antwortete „Alles klar, ich kümmere mich sofort
… ich plane jetzt die Entwicklung" — **ohne einen einzigen Werkzeugaufruf**. Auf
die Nachfrage „und blödelst du" wieder: „Nein, ich arbeite ernsthaft, ich
erstelle und deploye sie jetzt" — wieder nichts. Erst auf „Hast du die App
gebaut!!!???" sah er nach und gab zu: „Nein, wurde noch nicht gebaut."

Im **Auftrags**-Pfad ist genau das seit v1.178.2 abgesichert
(``llm_runner._compliance_gaps``) — dort standen beim Kunden zwei Auftraege auf
„erledigt", in deren Ergebnis woertlich „nur angekuendigt" stand. Der **Chat**-
Pfad hat diese Absicherung nie bekommen, und die Sprachfront laeuft ueber den
Chat.

**Warum die Pruefung hier enger sein muss als beim Auftrag.** Ein Auftrag ohne
Werkzeugaufruf ist immer verdaechtig — er ist zum Arbeiten da. Im Chat ist Reden
der Normalfall: „hallo", „wie geht's", „erklaer mir X" brauchen kein Werkzeug.
Ein Anstupser bei jedem werkzeuglosen Zug waere teuer und laestig.

Ausloeser ist deshalb nicht die Abwesenheit von Arbeit, sondern der WIDERSPRUCH:
der Agent sagt im selben Zug zu, jetzt etwas zu tun — und tut es nicht. Das ist
nachweisbar falsch, egal worum es geht.
"""

from __future__ import annotations

import re

#: Wendungen, mit denen ein Agent Arbeit fuer JETZT zusagt. Bewusst auf die
#: erste Person und die Gegenwart begrenzt: „ich erstelle jetzt", nicht „man
#: koennte erstellen" und nicht „ich habe erstellt".
#:
#: Deutsch und Englisch, weil dieselben Agenten in beiden Sprachen antworten.
_ZUSAGEN = (
    r"ich (?:kümmere|kuemmere) mich (?:jetzt|sofort|gleich|direkt|umgehend)",
    r"ich (?:mache|erledige|starte|beginne) (?:das |die |den |es )?(?:jetzt|sofort|gleich|direkt)",
    r"ich (?:erstelle|baue|entwickle|schreibe|deploye|implementiere|lege) [^.!?]{0,60}(?:jetzt|sofort|gleich|direkt)",
    r"(?:jetzt|sofort|gleich) (?:erstelle|baue|entwickle|schreibe|deploye|implementiere) ich",
    r"ich (?:plane|beginne) (?:jetzt|sofort) (?:die|den|das|mit)",
    r"i(?:'| a)?m (?:now )?(?:going to|about to) (?:build|create|write|deploy|implement|start)",
    r"i(?:'ll| will) (?:now |go ahead and )?(?:build|create|write|deploy|implement|start)",
    r"let me (?:build|create|write|deploy|implement|start) (?:that|this|it) now",
)

_ZUSAGE_RE = re.compile("|".join(_ZUSAGEN), re.IGNORECASE)

#: Werkzeuge, die fuer sich genommen KEINE Arbeit an der Sache sind. Wer nur
#: nachschlaegt, wo er steht, hat noch nichts gebaut — genau so entstand der
#: Eindruck von Arbeit im Bericht oben (drei ``read_file`` auf die eigene
#: Wissensdatei, sonst nichts).
_NUR_ORIENTIERUNG = frozenset({
    "search_memory", "save_memory", "brain_search", "brain_get", "brain_list",
    "search_tools", "list_tasks", "list_my_team", "get_context",
})


def promises_but_does_nothing(text: str, tools_called: set[str] | None) -> bool:
    """Sagt dieser Zug Arbeit zu, ohne welche zu leisten?

    ``text`` ist die Antwort des Agenten, ``tools_called`` die Werkzeuge DIESES
    Zuges. Reines Nachschlagen zaehlt nicht als Arbeit — sonst genuegt ein Blick
    in die eigene Wissensdatei, um die Pruefung zu bestehen.
    """
    if not text or not text.strip():
        return False
    echte_arbeit = {t for t in (tools_called or set()) if t not in _NUR_ORIENTIERUNG}
    if echte_arbeit:
        return False
    return bool(_ZUSAGE_RE.search(text))


#: Was der Agent daraufhin zu hoeren bekommt. Kurz, konkret, ohne Vorwurf — er
#: soll anfangen, nicht sich rechtfertigen.
NUDGE = (
    "Du hast gerade zugesagt, das JETZT zu tun, aber keinen einzigen Schritt "
    "unternommen. Ankuendigen ist nicht Tun. Fang jetzt an: lege die Aufgabe an "
    "(create_task/delegate_and_wait), oder mach es selbst — Dateien schreiben, "
    "Befehle ausfuehren. Wenn du es nicht kannst, sag WARUM, statt es "
    "anzukuendigen."
)
