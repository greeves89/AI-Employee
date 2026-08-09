"""Bewertung der Golden-Tests — die Rechnung, ohne Datenbank und ohne Netz (#391).

Alles hier ist rein: gleiche Eingabe, gleiche Ausgabe, immer. Das ist keine
Stilfrage, sondern der Zweck der Sache. Ein Gatter, dessen Bewertung schwankt,
blockiert mal und lässt mal durch — und dann glaubt ihm zu Recht niemand mehr.

**Warum keine Modell-Bewertung als Gatter.** Ein Sprachmodell als Schiedsrichter
liefert bei derselben Antwort mal 7 und mal 9 Punkte. Bei einem Schwellwert von 8
entscheidet dann der Zufall darüber, ob ein Update durchgeht. Deshalb zählt für das
Gatter ausschliesslich, was nachprüfbar ist: steht die Zahl drin, fehlt der
verbotene Satz, passt die Form. Eine Modell-Bewertung ist als **Hinweis** möglich
(``judge``), sie geht aber bewusst NICHT in den Wert ein, der blockiert.
"""

import re

# Ein Lauf gilt als Rückschritt, wenn er mehr als das unter der Grundlinie liegt.
# Nicht null: ein Test, der an einer Zeitangabe oder einer Formulierung haengt,
# schwankt um ein, zwei Punkte, ohne dass sich etwas verschlechtert haette.
DEFAULT_TOLERANCE = 5.0


def check_item(item: dict, answer: str | None) -> dict:
    """Eine einzelne Aufgabe bewerten.

    Rückgabe enthält IMMER ``checks`` — auch bei Erfolg. Ein Fehlschlag ohne
    Begründung ist wertlos: dann steht da „Test rot", und jemand muss von Hand
    nachstellen, was eigentlich erwartet war.
    """
    text = answer or ""
    haystack = text.lower()
    checks: list[dict] = []

    for needle in item.get("expect_contains") or []:
        ok = str(needle).lower() in haystack
        checks.append({"kind": "contains", "value": needle, "ok": ok})

    for needle in item.get("expect_absent") or []:
        ok = str(needle).lower() not in haystack
        checks.append({"kind": "absent", "value": needle, "ok": ok})

    for pattern in item.get("expect_regex") or []:
        try:
            ok = re.search(str(pattern), text, re.IGNORECASE | re.MULTILINE) is not None
            checks.append({"kind": "regex", "value": pattern, "ok": ok})
        except re.error as e:
            # Ein kaputtes Muster ist ein Fehler in der Sammlung, kein Fehler des
            # Agenten. Es als „bestanden" zu werten waere schlimmer: dann verdeckt
            # ein Tippfehler eine echte Verschlechterung.
            checks.append({
                "kind": "regex", "value": pattern, "ok": False,
                "error": f"Ungültiger Ausdruck: {e}",
            })

    min_len = item.get("min_length")
    if min_len:
        checks.append({
            "kind": "min_length", "value": min_len, "ok": len(text.strip()) >= int(min_len),
        })

    if not checks:
        # Eine Aufgabe ohne einzige Erwartung kann nicht durchfallen — und wuerde
        # den Wert stillschweigend nach oben ziehen. Das ist ein Fehler in der
        # Sammlung und wird als solcher gemeldet.
        checks.append({
            "kind": "none", "value": None, "ok": False,
            "error": "Aufgabe ohne Erwartung — sie kann nichts pruefen",
        })

    ok = all(c["ok"] for c in checks)
    return {
        "id": item.get("id") or item.get("title") or "",
        "title": item.get("title") or "",
        "ok": ok,
        "weight": _weight(item),
        "checks": checks,
        "answer_excerpt": " ".join(text.split())[:400],
    }


def _weight(item: dict) -> float:
    """Gewicht einer Aufgabe. Unbrauchbare Angaben zählen als 1."""
    try:
        value = float(item.get("weight", 1) or 1)
    except (TypeError, ValueError):
        return 1.0
    if value <= 0 or value != value:
        return 1.0
    return min(value, 100.0)


def score_results(results: list[dict]) -> float:
    """Gewichteter Anteil bestandener Aufgaben, 0–100.

    Gewichtet, weil nicht jede Aufgabe gleich viel wiegt: „Umsatzsteuer korrekt
    berechnen" ist wichtiger als „Grussformel vorhanden". Ohne Gewichte müsste man
    wichtige Aufgaben mehrfach hinschreiben.
    """
    if not results:
        return 0.0
    total = sum(r.get("weight", 1.0) for r in results)
    if total <= 0:
        return 0.0
    got = sum(r.get("weight", 1.0) for r in results if r.get("ok"))
    return round(100.0 * got / total, 1)


def is_regression(score: float, baseline: float | None, tolerance: float = DEFAULT_TOLERANCE) -> bool:
    """Ist das ein Rückschritt gegenüber der Grundlinie?

    Ohne Grundlinie ist es **keiner** — der erste Lauf kann nichts unterschreiten.
    Ihn zu blockieren hiesse, dass niemand je anfangen kann.
    """
    if baseline is None:
        return False
    return score < (baseline - tolerance)


def gate_decision(
    *,
    score: float | None,
    baseline: float | None,
    tolerance: float = DEFAULT_TOLERANCE,
    require_run: bool = False,
) -> dict:
    """Darf das Update durch?

    ``require_run`` unterscheidet die zwei sinnvollen Haltungen:

    * **aus** (Vorgabe) — wer keine Golden-Tests angelegt hat, wird nicht
      ausgebremst. Ein Gatter, das jedes Update blockiert, wird binnen einer Woche
      abgeschaltet und schützt dann gar nichts mehr.
    * **an** — für Rollen, bei denen ein unbemerkter Rückschritt teuer ist. Dann
      ist „kein Lauf vorhanden" selbst ein Grund zu blockieren.
    """
    if score is None:
        if require_run:
            return {
                "allowed": False,
                "reason": "no_run",
                "message": (
                    "Kein Testlauf vorhanden. Für diese Rolle ist ein bestandener "
                    "Lauf Voraussetzung für das Update."
                ),
            }
        return {"allowed": True, "reason": "no_run", "message": "Keine Golden-Tests hinterlegt."}

    if is_regression(score, baseline, tolerance):
        return {
            "allowed": False,
            "reason": "regression",
            "message": (
                f"Rückschritt: {score:.1f} statt {baseline:.1f} Punkte "
                f"(Toleranz {tolerance:.0f}). Das Update würde die Rolle verschlechtern."
            ),
        }

    if baseline is None:
        return {
            "allowed": True, "reason": "first_run",
            "message": f"Erster Lauf mit {score:.1f} Punkten — wird zur Grundlinie.",
        }
    return {
        "allowed": True, "reason": "ok",
        "message": f"{score:.1f} Punkte, Grundlinie {baseline:.1f} — kein Rückschritt.",
    }


def validate_items(items) -> list[dict]:
    """Eine Aufgabensammlung prüfen und säubern.

    Wirft bei allem, was später still danebengehen würde. Eine Sammlung, die halb
    stimmt, ist schlimmer als keine: sie liefert einen Wert, dem man glaubt.
    """
    if not isinstance(items, list) or not items:
        raise ValueError("Die Sammlung braucht mindestens eine Aufgabe")
    if len(items) > 100:
        raise ValueError("Höchstens 100 Aufgaben je Sammlung")

    cleaned: list[dict] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise ValueError(f"Aufgabe {index + 1} ist kein Objekt")
        prompt = str(raw.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"Aufgabe {index + 1} hat keinen Auftragstext")
        if len(prompt) > 8000:
            raise ValueError(f"Aufgabe {index + 1}: Auftragstext ist zu lang")

        item_id = str(raw.get("id") or f"i{index + 1}").strip()
        # Die Kennung landet in Protokollen und in der Zuordnung Auftrag→Aufgabe.
        # Zeilenumbrüche darin waeren eine Protokoll-Faelschung mit Ansage, und ein
        # Leerzeichen macht die Zuordnung unlesbar.
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", item_id):
            raise ValueError(
                f"Aufgabe {index + 1}: Kennung darf nur Buchstaben, Ziffern und "
                f"-_.: enthalten (höchstens 64 Zeichen)"
            )
        if item_id in seen_ids:
            # Doppelte Kennungen wuerden beim Zuordnen der Antworten die falsche
            # Aufgabe treffen — und der Wert waere still falsch.
            raise ValueError(f"Kennung „{item_id}“ kommt mehrfach vor")
        seen_ids.add(item_id)

        item = {
            "id": item_id,
            "title": str(raw.get("title") or "").strip()[:200] or item_id,
            "prompt": prompt,
            "weight": _weight(raw),
        }
        for key in ("expect_contains", "expect_absent", "expect_regex"):
            values = raw.get(key)
            if values is None:
                continue
            if not isinstance(values, list):
                raise ValueError(f"Aufgabe {item_id}: {key} muss eine Liste sein")
            item[key] = [str(v) for v in values if str(v).strip()]
        for pattern in item.get("expect_regex") or []:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Aufgabe {item_id}: ungültiger Ausdruck — {e}")
        if raw.get("min_length"):
            try:
                item["min_length"] = max(1, int(raw["min_length"]))
            except (TypeError, ValueError):
                raise ValueError(f"Aufgabe {item_id}: min_length muss eine Zahl sein")
        if raw.get("judge"):
            # Hinweis, kein Gatter — siehe Modulkopf.
            item["judge"] = str(raw["judge"])[:1000]

        if not any(k in item for k in ("expect_contains", "expect_absent", "expect_regex", "min_length")):
            raise ValueError(
                f"Aufgabe {item_id} hat keine Erwartung — sie könnte nie durchfallen"
            )
        cleaned.append(item)
    return cleaned
