"""``parent_task_id`` wurde im Werkzeug angeboten und in der Nutzlast verworfen (#880)."""

from app.tools.api_client import OrchestratorAPIClient as ApiClient


def test_angabe_kommt_in_der_nutzlast_an():
    nutzlast = ApiClient._task_payload({"title": "t", "prompt": "p", "parent_task_id": "t-1"})
    assert nutzlast["parent_task_id"] == "t-1"


def test_ohne_angabe_kein_leeres_feld():
    """Leer mitgeschickt, wuerde der Server nichts ableiten koennen."""
    assert "parent_task_id" not in ApiClient._task_payload({"title": "t", "prompt": "p"})
    assert "parent_task_id" not in ApiClient._task_payload({"title": "t", "prompt": "p", "parent_task_id": ""})
