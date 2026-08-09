"""Jeder Import im Orchestrator muss auflösbar sein.

Der Anlass: ``app/core/memory_preload.py`` importierte seit dem 2026-08-07 aus
``app.models.agent_memory`` — ein Modul, das es nicht gibt (es heisst
``app.models.memory``). Der Gedaechtnis-Preload war damit unbenutzbar, und zwar
unbemerkt: der Import steht auf Modulebene, aber das Modul wird selbst erst spaet
importiert, und die vorhandenen Tests haben es nie angefasst.

Genau dieselbe Falle wie die 160 Stellen mit verschlucktem Import — nur hier ohne
except, also als 500 statt als stiller Ausfall.

Dieser Test importiert JEDES Modul unter app/ einmal. Das ist der billigste Weg,
diese Klasse Fehler beim Testlauf zu fangen statt beim Nutzer.
"""

import importlib
import pkgutil
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]

# Module, die beim Import Fremdsysteme brauchen und deshalb ausgenommen sind.
# Bewusst kurz gehalten — jede Ausnahme ist ein Stueck ungetesteter Code.
SKIP_PREFIXES = (
    "app.services.voice_providers.realtime_nova_sonic",  # aws-sdk-bedrock-runtime
)


def _module_names() -> list[str]:
    names = []
    app_dir = ORCH / "app"
    for mod in pkgutil.walk_packages([str(app_dir)], prefix="app."):
        if mod.ispkg:
            continue
        if mod.name.startswith(SKIP_PREFIXES):
            continue
        names.append(mod.name)
    return sorted(names)


class ImportTests(unittest.TestCase):
    def test_every_module_imports(self):
        """Ein nicht aufloesbarer Import ist immer ein Fehler, nie ein Zustand."""
        broken: list[str] = []
        for name in _module_names():
            try:
                importlib.import_module(name)
            except ModuleNotFoundError as e:
                # Fehlende FREMDpakete sind ein Umgebungsproblem, kein Codefehler —
                # nur ein fehlendes app.*-Modul zaehlt hier.
                missing = str(e).split("'")[1] if "'" in str(e) else str(e)
                if missing.startswith("app."):
                    broken.append(f"{name}: {e}")
            except Exception:  # noqa: BLE001 — andere Startfehler pruefen andere Tests
                pass
        self.assertEqual(broken, [], "Nicht aufloesbare app-Importe:\n" + "\n".join(broken))

    def test_the_memory_model_lives_where_it_is_imported_from(self):
        """Der konkrete Fall, der das ausgeloest hat."""
        from app.core.memory_preload import AgentMemory
        from app.models.memory import AgentMemory as FromModel

        self.assertIs(AgentMemory, FromModel)

    def test_no_module_imports_the_nonexistent_path(self):
        """Nur echte Import-Zeilen — ein Kommentar, der den Fehler erklaert, ist
        genau das, was hier stehen bleiben SOLL."""
        import re

        pattern = re.compile(r"^\s*(from|import)\s+app\.models\.agent_memory\b", re.M)
        hits = [
            str(path.relative_to(ORCH))
            for path in (ORCH / "app").rglob("*.py")
            if pattern.search(path.read_text())
        ]
        self.assertEqual(hits, [], f"Falscher Modulpfad noch importiert in: {hits}")


if __name__ == "__main__":
    unittest.main()
