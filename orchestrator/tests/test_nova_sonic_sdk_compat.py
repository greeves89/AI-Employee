"""Der Nova-Sonic-Client muss mit beiden SDK-Generationen konstruieren.

aws-sdk-bedrock-runtime 0.10 hat ``Config`` -> ``AsyncBedrockRuntimeConfig``
und ``BedrockRuntimeClient`` -> ``AsyncBedrockRuntimeClient`` umbenannt (Feld-
namen und auth/models blieben gleich). Auf einer Anlage mit 0.10 im Image lag
deshalb JEDE Sprachsitzung tot: "cannot import name 'Config'". Dieser Test
faehrt den echten ``_config()``-Pfad gegen die tatsaechlich installierte
SDK-Version — egal welche Generation — statt Importzeilen zu zaehlen.
"""

import asyncio

import pytest


def test_config_constructs_with_installed_sdk():
    pytest.importorskip("aws_sdk_bedrock_runtime")
    from app.services.voice_providers.realtime_nova_sonic import NovaSonicSession

    session = object.__new__(NovaSonicSession)
    session._access_key = "AKIA-test"
    session._secret_key = "test-secret"
    session._session_token = None
    session.region = "us-east-1"

    cfg = asyncio.run(session._config())

    assert cfg.region == "us-east-1"
    assert "bedrock-runtime.us-east-1" in str(cfg.endpoint_uri)
