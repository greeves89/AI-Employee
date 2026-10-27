"""Tests der Bridge beruehren NIE die installierte Bridge des Rechners.

Ohne das schrieben sie ins echte Protokoll (~/Library/Logs/ai-employee/bridge.log)
und pinnten Zertifikate in die echte Konfiguration (~/.ai_employee_bridge.json).
Muss vor jedem Import von bridge/tray_app gesetzt sein — conftest laedt zuerst.
"""

import os
import tempfile

_ABLAGE = tempfile.mkdtemp(prefix="bridge-tests-")
os.environ["AI_EMPLOYEE_BRIDGE_LOG_DIR"] = _ABLAGE
os.environ["AI_EMPLOYEE_BRIDGE_CONFIG"] = os.path.join(_ABLAGE, "bridge-config.json")
