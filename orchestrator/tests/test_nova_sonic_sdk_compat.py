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


def test_transport_supports_duplex_streaming():
    """Konstruieren reicht nicht — der Transport muss Zwei-Wege-Streaming koennen.

    Der Test darueber baut die Config erfolgreich auf, auch wenn die Sprach-
    funktion vollstaendig tot ist: die Uebertragungsart faellt erst beim Oeffnen
    des Streams auf. Genau so passierte es mit SDK 0.11, das die Voreinstellung
    von ``AWSCRTHTTPClient`` auf ``AIOHTTPClient`` umstellte und ``awscrt`` zum
    optionalen Extra machte — jede Sprachsitzung starb an
    ``UnsupportedTransportError``, waehrend Build und Tests gruen blieben.
    """
    pytest.importorskip("aws_sdk_bedrock_runtime")
    from app.services.voice_providers.realtime_nova_sonic import NovaSonicSession

    session = object.__new__(NovaSonicSession)
    session._access_key = "AKIA-test"
    session._secret_key = "test-secret"
    session._session_token = None
    session.region = "us-east-1"

    cfg = asyncio.run(session._config())

    assert getattr(cfg.transport, "SUPPORTS_DUPLEX_STREAMING", False), (
        f"{type(cfg.transport).__name__} kann kein Zwei-Wege-Streaming — "
        "Nova Sonic braucht es fuer InvokeModelWithBidirectionalStream"
    )
