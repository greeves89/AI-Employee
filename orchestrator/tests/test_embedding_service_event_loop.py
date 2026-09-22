"""Der Embedding-Service darf seinen Event-Loop nicht mit dem Modell blockieren.

Befund #826: ``embed_batch`` war ``async def`` und rief das synchrone, CPU-
gebundene ``_model.encode`` direkt. Solange ein Batch lief, konnte der Service
nicht einmal ``/healthz`` beantworten -- Docker meldete ihn nach drei Fehlschlaegen
als unhealthy, die Oberflaeche zeigte "Degraded", obwohl er nur arbeitete. Und
ein Aufrufer, der nach 300 s aufgab, stoppte die Arbeit nicht: der Container
rechnete ueber eine Stunde bei 190 % CPU weiter, ohne dass jemand auf das
Ergebnis wartete.

Der Service hat keinen eigenen Testlauf; das Modul wird hier ueber den Pfad
geladen (der Modellimport liegt im Lifespan und wird nicht angefasst). Ein
Fake-Modell mit ``time.sleep`` steht fuer den echten CPU-Lauf.
"""

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

import numpy as np
import pytest

_PFAD = Path(__file__).resolve().parents[2] / "embedding-service" / "app" / "main.py"


def _lade_modul():
    spec = importlib.util.spec_from_file_location("embedding_service_main", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    return modul


class _LangsamesModell:
    """Blockiert wie ein echter Modellaufruf und zaehlt die Aufrufe."""

    def __init__(self, dauer: float):
        self.dauer = dauer
        self.aufrufe: list[int] = []

    def encode(self, texts, **_):
        self.aufrufe.append(len(texts))
        time.sleep(self.dauer)
        return np.zeros((len(texts), 4), dtype=np.float32)

    def get_sentence_embedding_dimension(self):
        return 4


@pytest.fixture
def svc(monkeypatch):
    modul = _lade_modul()
    monkeypatch.setattr(modul, "SUB_BATCH_SIZE", 2)
    return modul


@pytest.mark.asyncio
async def test_healthz_antwortet_waehrend_ein_batch_rechnet(svc, monkeypatch):
    """Vorher: /healthz haengt hinter dem Batch. Nachher: antwortet sofort."""
    import httpx

    modell = _LangsamesModell(dauer=0.4)
    monkeypatch.setattr(svc, "_model", modell)
    transport = httpx.ASGITransport(app=svc.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://svc") as client:
        t0 = time.monotonic()  # VOR dem Start: eine Blockade wuerde schon das
        batch = asyncio.create_task(  # asyncio.sleep unten mit aufhalten
            client.post("/embed/batch", json={"texts": ["a", "b"]})
        )
        await asyncio.sleep(0.05)  # der Batch steckt jetzt im Modellaufruf
        health = await asyncio.wait_for(client.get("/healthz"), timeout=5)
        health_dauer = time.monotonic() - t0
        batch_noch_offen = not batch.done()
        antwort = await batch

    assert health.status_code == 200
    assert antwort.status_code == 200 and antwort.json()["count"] == 2
    # Vorbedingung: der Batch hat wirklich so lange gedauert wie das Modell --
    # sonst haette der Test die Blockade gar nicht sehen koennen.
    assert modell.aufrufe == [2]
    assert batch_noch_offen, "/healthz kam erst dran, als der Batch schon fertig war"
    assert health_dauer < 0.3, f"/healthz wartete {health_dauer:.2f}s auf den Batch"


@pytest.mark.asyncio
async def test_batch_stoppt_wenn_der_aufrufer_weg_ist(svc, monkeypatch):
    """Zwischen zwei Teil-Batches wird nachgesehen, ob noch jemand wartet."""
    modell = _LangsamesModell(dauer=0.0)
    monkeypatch.setattr(svc, "_model", modell)
    fragen = []

    async def weg_ab_der_zweiten_frage():
        fragen.append(1)
        return len(fragen) >= 2

    with pytest.raises(svc.ClientGone):
        await svc._encode_batch_abortable(
            ["t1", "t2", "t3", "t4", "t5", "t6"], True, weg_ab_der_zweiten_frage
        )
    # Teil-Batches: [t1,t2] ohne Frage, [t3,t4] nach Frage 1 (nein), Frage 2 (ja) -> Abbruch
    assert modell.aufrufe == [2, 2], modell.aufrufe


@pytest.mark.asyncio
async def test_batch_laeuft_durch_wenn_der_aufrufer_bleibt(svc, monkeypatch):
    modell = _LangsamesModell(dauer=0.0)
    monkeypatch.setattr(svc, "_model", modell)

    async def bleibt():
        return False

    vecs = await svc._encode_batch_abortable(["t1", "t2", "t3"], True, bleibt)
    assert len(vecs) == 3 and modell.aufrufe == [2, 1]


@pytest.mark.asyncio
async def test_einzel_embed_liefert_flachen_vektor(svc, monkeypatch):
    """/embed ging vorher direkt ueber encode(text); jetzt ueber den Batch-Weg --
    das Ergebnis muss ein flacher Vektor bleiben, kein [[...]]."""
    import httpx

    monkeypatch.setattr(svc, "_model", _LangsamesModell(dauer=0.0))
    transport = httpx.ASGITransport(app=svc.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://svc") as client:
        r = await client.post("/embed", json={"text": "hallo"})
    assert r.status_code == 200
    assert r.json()["dimension"] == 4 and r.json()["embedding"] == [0.0, 0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_modellaufrufe_laufen_nacheinander_nicht_parallel(svc, monkeypatch):
    """Zwei gleichzeitige Batches teilen sich das Modell: Aufrufe ueberlappen nie.

    Parallele encode()-Aufrufe auf demselben Modul kaempfen nur um dieselben
    Kerne (und sentence-transformers ist dafuer nicht ausgelegt)."""
    import threading

    class _Zaehlt(_LangsamesModell):
        def __init__(self):
            super().__init__(dauer=0.05)
            self.aktiv = 0
            self.max_aktiv = 0
            self._lock = threading.Lock()

        def encode(self, texts, **kw):
            with self._lock:
                self.aktiv += 1
                self.max_aktiv = max(self.max_aktiv, self.aktiv)
            try:
                return super().encode(texts, **kw)
            finally:
                with self._lock:
                    self.aktiv -= 1

    modell = _Zaehlt()
    monkeypatch.setattr(svc, "_model", modell)

    async def bleibt():
        return False

    await asyncio.gather(*(
        svc._encode_batch_abortable(["a", "b", "c"], True, bleibt) for _ in range(4)
    ))
    assert len(modell.aufrufe) == 8  # 4 Batches x 2 Teil-Batches
    assert modell.max_aktiv == 1, f"{modell.max_aktiv} Modellaufrufe gleichzeitig"


def test_endpunkte_rufen_das_modell_nicht_mehr_direkt(svc):
    """Formpruefung: kein ``_model.encode(`` mehr in einer async-Funktion --
    der einzige Aufruf steht im Threadpool-Helfer."""
    import ast

    baum = ast.parse(_PFAD.read_text())
    direkt = []
    for fn in ast.walk(baum):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for k in ast.walk(fn):
            if (isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute)
                    and k.func.attr == "encode"
                    and isinstance(k.func.value, ast.Name) and k.func.value.id == "_model"):
                direkt.append(fn.name)
    assert direkt == [], f"synchroner encode()-Aufruf im Event-Loop: {direkt}"
