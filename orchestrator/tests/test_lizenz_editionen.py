"""Die Editionen der Lizenz — jede verkaufte Stufe muss der Verifizierer kennen.

Mit dem Lizenzmodell vom 01.10.2026 kam „Starter" als kleinste gewerbliche
Edition dazu. Eine Stufe, die der Lizenzserver ausstellt, die Anlage aber nicht
kennt, fiele still auf die Community-Rechte zurueck — fuer Starter waere das
zufaellig dasselbe, fuer jede kuenftige Stufe mit eigenen Rechten nicht.
"""

from app.core.license import (
    BUSINESS_FEATURES,
    COMMUNITY_FEATURES,
    ENTERPRISE_FEATURES,
    TEAM_FEATURES,
    License,
    _get_features_for_tier,
)


def test_jede_verkaufte_edition_ist_bekannt():
    for stufe in ("starter", "team", "business", "enterprise"):
        assert _get_features_for_tier(stufe) >= COMMUNITY_FEATURES, stufe


def test_starter_ist_die_volle_plattform_ohne_zusatzrechte():
    assert _get_features_for_tier("starter") == COMMUNITY_FEATURES


def test_die_stufen_bauen_aufeinander_auf():
    assert COMMUNITY_FEATURES <= TEAM_FEATURES <= BUSINESS_FEATURES <= ENTERPRISE_FEATURES


def test_unbekannte_stufe_bekommt_nur_die_grundrechte():
    assert _get_features_for_tier("gibt-es-nicht") == COMMUNITY_FEATURES


def test_abgelaufene_lizenz_faellt_auf_die_grundrechte_zurueck():
    lizenz = License(tier="business", features=BUSINESS_FEATURES, expires_at="2020-01-01T00:00:00Z")
    assert lizenz.is_expired
    assert not lizenz.has_feature("sso_microsoft")
    assert lizenz.has_feature("multi_agent")
