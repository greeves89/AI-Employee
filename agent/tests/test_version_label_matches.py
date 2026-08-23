"""Die Versionsnummer steht an zwei Stellen — sie sind schon auseinandergelaufen.

`VERSION` und das `LABEL ai-employee.version` im Agent-Dockerfile muessen dieselbe
Nummer tragen. Sonst zeigt ein laufendes Image eine Version an, die es nicht ist, und
ein Betreiber kann nicht mehr feststellen, was auf seiner Anlage liegt. Genau das war
der Fall (VERSION 1.264.3 gegen LABEL 1.264.0); dort half nur ein Abgleich von Hand.

Der Abgleich von Hand faellt beim naechsten Mal wieder aus. Deshalb dieser Test.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

LABEL_RE = re.compile(r'^LABEL\s+ai-employee\.version\s*=\s*"([^"]+)"', re.MULTILINE)


def _version_file() -> str:
    return (REPO / "VERSION").read_text(encoding="utf-8").strip()


def _dockerfile_label() -> str:
    src = (REPO / "agent" / "Dockerfile").read_text(encoding="utf-8")
    match = LABEL_RE.search(src)
    assert match, "agent/Dockerfile: kein 'LABEL ai-employee.version=\"...\"' gefunden"
    return match.group(1).strip()


def test_version_file_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _version_file()), (
        f"VERSION ist kein SemVer: {_version_file()!r}"
    )


def test_dockerfile_label_matches_version_file():
    version, label = _version_file(), _dockerfile_label()
    assert label == version, (
        f"VERSION ({version}) und agent/Dockerfile LABEL ai-employee.version ({label}) "
        "laufen auseinander — beide im selben Zug erhoehen."
    )
