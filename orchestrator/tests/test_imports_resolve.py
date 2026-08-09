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
import sys
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


def _internal_imports() -> list[tuple[str, int, str, str]]:
    """Jeder ``app.*``-Import im Quelltext — auch die INNERHALB von Funktionen.

    Der Modulimport-Test unten deckt nur Importe auf Modulebene ab. Genau die
    gefaehrlichen stehen aber oft in einer Funktion, hinter einem breiten
    ``except Exception`` (172 solche Bloecke gibt es hier) — dort faellt ein
    falscher Pfad nie auf, weil er entweder verschluckt wird oder erst beim Aufruf
    zuschlaegt. Deshalb wird hier STATISCH geprueft, nicht durch Ausfuehren.
    """
    import ast

    out: list[tuple[str, int, str, str]] = []
    for path in sorted((ORCH / "app").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        rel = str(path.relative_to(ORCH))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("app.") and node.level == 0:
                    for alias in node.names:
                        out.append((rel, node.lineno, node.module, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app."):
                        out.append((rel, node.lineno, alias.name, ""))
    return out


def _module_file_exists(module: str) -> bool:
    """Gibt es zu ``app.x.y`` eine Datei auf der Platte?

    Bewusst ueber den Dateipfad statt ueber ``importlib.util.find_spec``: andere
    Tests haengen Attrappen in ``sys.modules`` (etwa fuer den Einbettungsdienst, den
    es lokal nicht gibt). ``find_spec`` stolpert darueber mit ``__spec__ is None``,
    und der Test hing damit davon ab, welche Datei vorher lief. Ein Blick auf die
    Platte kennt diese Reihenfolge nicht.
    """
    stem = ORCH / Path(*module.split("."))
    return stem.with_suffix(".py").exists() or (stem / "__init__.py").exists()


def _is_stub(module: str) -> bool:
    """Attrappe eines anderen Tests? Dann sagt sie nichts ueber echte Namen aus."""
    mod = sys.modules.get(module)
    return mod is not None and getattr(mod, "__spec__", None) is None


class StaticImportTests(unittest.TestCase):
    """Verweist jeder app.*-Import auf ein Modul, das es wirklich gibt?

    Der konkrete Anlass: ``from app.models.agent_memory import AgentMemory`` — das
    Modul heisst ``app.models.memory``. Zwei Tage unbemerkt, weil der Import erst
    beim Aufruf zuschlaegt.
    """

    def test_every_internal_import_target_exists(self):
        broken = []
        for rel, line, module, _name in _internal_imports():
            if not _module_file_exists(module):
                broken.append(f"{rel}:{line} → {module}")
        self.assertEqual(
            broken, [],
            "Importe auf nicht existierende Module:\n" + "\n".join(broken),
        )

    def test_every_imported_name_exists(self):
        """Auch der NAME muss stimmen — ein richtiges Modul mit falscher Klasse
        scheitert genauso, nur noch spaeter."""
        import importlib

        broken = []
        for rel, line, module, name in _internal_imports():
            if not name or name == "*" or _is_stub(module):
                continue
            try:
                mod = importlib.import_module(module)
            except Exception:  # noqa: BLE001 — Ladefehler prueft der andere Test
                continue
            if not hasattr(mod, name):
                # Untermodul statt Attribut ist zulaessig (from app.api import ws).
                try:
                    importlib.import_module(f"{module}.{name}")
                except Exception:  # noqa: BLE001
                    broken.append(f"{rel}:{line} → {module}.{name}")
        self.assertEqual(
            broken, [],
            "Importierte Namen, die es nicht gibt:\n" + "\n".join(broken),
        )


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
