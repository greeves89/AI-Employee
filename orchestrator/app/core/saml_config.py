"""SAML 2.0 — Konfiguration des Dienstanbieters und Zuordnung von IdP-Gruppen.

Warum eine Bibliothek und kein Eigenbau: SAML-Signaturen sind XML-DSig. Die Pruefung
selbst zu schreiben ist der klassische Ort fuer Signature-Wrapping (XSW) — ein
Kanonisierungsfehler dort ist kein Schoenheitsfehler, sondern ein Authentifizierungs-
Bypass. Das uebernimmt ``python3-saml`` (auf ``xmlsec`` aufsetzend).

Was hier liegt, ist nur das Drumherum: die Angaben des Identitaetsanbieters aus den
Einstellungen in die Form bringen, die die Bibliothek erwartet, und die Gruppen des
Anbieters auf Rollen dieser Plattform abbilden.

Die Nutzeraufloesung selbst passiert NICHT hier, sondern in
``SSOService._find_or_create_user`` — demselben Weg, den auch OIDC nimmt. Ein zweiter
Weg, auf dem Konten entstehen koennen, waere genau die Stelle, an der Rechte
auseinanderlaufen.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

PROVIDER_NAME = "saml"
DISPLAY_NAME_SETTING = "saml_display_name"

# Angaben des Identitaetsanbieters (kommen aus dessen Metadaten).
IDP_ENTITY_ID = "saml_idp_entity_id"
IDP_SSO_URL = "saml_idp_sso_url"
IDP_SLO_URL = "saml_idp_slo_url"
IDP_CERT = "saml_idp_x509_cert"

# Angaben zu UNS als Dienstanbieter.
SP_ENTITY_ID = "saml_sp_entity_id"

# Gruppen-Abbildung: aus welchem Attribut die Gruppen kommen und was sie bedeuten.
GROUP_ATTRIBUTE = "saml_group_attribute"
GROUP_ROLE_MAP = "saml_group_role_map"        # JSON {"Gruppenname": "admin"|"manager"|"member"}

_DEFAULT_GROUP_ATTRIBUTE = "groups"

# Nur diese Attributnamen werden fuer E-Mail und Namen akzeptiert. Identitaetsanbieter
# benennen sie unterschiedlich; die lange URN-Form kommt von ADFS und Entra ID.
_EMAIL_ATTRS = (
    "email",
    "emailAddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
)
_NAME_ATTRS = (
    "displayName",
    "name",
    "cn",
    "urn:oid:2.5.4.3",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
)


async def load_settings(db: AsyncSession) -> dict:
    """Die hinterlegten SAML-Angaben als schlichtes Woerterbuch."""
    from app.services.settings_service import SettingsService

    svc = SettingsService(db)
    keys = (IDP_ENTITY_ID, IDP_SSO_URL, IDP_SLO_URL, IDP_CERT, SP_ENTITY_ID,
            GROUP_ATTRIBUTE, GROUP_ROLE_MAP, DISPLAY_NAME_SETTING)
    return {k: (await svc.get(k)) or "" for k in keys}


def is_configured(cfg: dict) -> bool:
    """Vollstaendig genug, um es dem Nutzer als Anmeldeweg anzubieten?

    Ohne Zertifikat waere die Signatur nicht pruefbar — dann darf der Knopf gar nicht
    erst erscheinen, statt beim Klick in einen Fehler zu laufen.
    """
    return bool(cfg.get(IDP_ENTITY_ID) and cfg.get(IDP_SSO_URL) and cfg.get(IDP_CERT))


def build_saml_settings(cfg: dict, base_url: str) -> dict:
    """Die Konfiguration in der Form, die ``python3-saml`` erwartet.

    Die Sicherheitsschalter sind bewusst gesetzt und nicht der Vorgabe ueberlassen:
    eine unsignierte Assertion anzunehmen macht die gesamte Anmeldung wertlos, weil
    dann jeder eine Antwort mit beliebiger E-Mail schicken koennte.
    """
    base = base_url.rstrip("/")
    sp_entity = cfg.get(SP_ENTITY_ID) or f"{base}/api/v1/auth/sso/saml/metadata"
    return {
        "strict": True,          # Fehler in der Antwort fuehren zur Ablehnung, nicht zur Warnung
        "debug": False,
        "sp": {
            "entityId": sp_entity,
            "assertionConsumerService": {
                "url": f"{base}/api/v1/auth/sso/saml/acs",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "singleLogoutService": {
                "url": f"{base}/api/v1/auth/sso/saml/sls",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        },
        "idp": {
            "entityId": cfg.get(IDP_ENTITY_ID, ""),
            "singleSignOnService": {
                "url": cfg.get(IDP_SSO_URL, ""),
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "singleLogoutService": {
                "url": cfg.get(IDP_SLO_URL, ""),
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": cfg.get(IDP_CERT, ""),
        },
        "security": {
            # Signatur ist Pflicht. Ohne diese beiden wuerde eine unsignierte oder
            # nur teilsignierte Antwort durchgehen — der Kern des ganzen Verfahrens.
            "wantMessagesSigned": False,     # der Rahmen darf unsigniert sein,
            "wantAssertionsSigned": True,    # der INHALT nicht
            "wantNameId": True,
            "requestedAuthnContext": False,
            "rejectUnsolicitedResponsesWithInResponseTo": True,
            "signMetadata": False,
            "authnRequestsSigned": False,
        },
    }


def extract_identity(attributes: dict, name_id: str) -> tuple[str, str]:
    """E-Mail und Anzeigename aus den Attributen, mit NameID als Rueckfall.

    Identitaetsanbieter benennen dieselben Felder unterschiedlich — deshalb eine
    Liste bekannter Namen statt einer einzigen Annahme.
    """
    def _first(names) -> str:
        for key in names:
            value = attributes.get(key)
            if isinstance(value, list) and value:
                return str(value[0]).strip()
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    email = _first(_EMAIL_ATTRS) or (name_id if "@" in (name_id or "") else "")
    name = _first(_NAME_ATTRS) or (email.split("@")[0] if email else "")
    return email, name


def extract_groups(attributes: dict, cfg: dict) -> list[str]:
    """Die Gruppen aus dem konfigurierten Attribut."""
    attr = cfg.get(GROUP_ATTRIBUTE) or _DEFAULT_GROUP_ATTRIBUTE
    raw = attributes.get(attr)
    if isinstance(raw, list):
        return [str(g).strip() for g in raw if str(g).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def role_for_groups(groups: list[str], group_role_map: dict) -> str | None:
    """Welche Rolle die Gruppen ergeben, oder ``None`` wenn keine passt.

    Die HOECHSTE zutreffende Rolle gewinnt: wer in „IT-Admins" UND „Alle Mitarbeiter"
    steht, soll Administrator sein und nicht zufaellig Mitglied, je nachdem wie das
    Verzeichnis die Gruppen sortiert. Ohne Treffer wird die Rolle NICHT angefasst —
    eine leere Zuordnung darf niemandem Rechte wegnehmen, die ein Mensch vergeben hat.
    """
    if not group_role_map:
        return None
    rank = {"admin": 3, "manager": 2, "member": 1}
    best: str | None = None
    lowered = {g.lower() for g in groups}
    for group, role in group_role_map.items():
        if str(group).lower() in lowered:
            role = str(role).lower()
            if role in rank and (best is None or rank[role] > rank[best]):
                best = role
    return best


def parse_group_role_map(raw: str) -> dict:
    """Die hinterlegte Zuordnung lesen. Kaputte Eingabe heisst „keine Zuordnung",
    nicht „Absturz beim Anmelden"."""
    import json

    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        logger.warning("SAML-Gruppenzuordnung ist kein gueltiges JSON — wird ignoriert")
        return {}
