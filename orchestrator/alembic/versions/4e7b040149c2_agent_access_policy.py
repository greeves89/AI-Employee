"""Autonomy-Matrix + Sudo-Berechtigungen in agents.access_policy buendeln (Issue #787)

Revision ID: 4e7b040149c2
Revises: 7a9c2e4f1b3d
Create Date: 2026-09-17

Ursprünglich als "c4d5e6f7a8b9" angelegt -- diese ID war bereits von
``c4d5e6f7a8b9_meeting_deliverable.py`` belegt (anderer Zweig, anderes
``down_revision``). Alembic akzeptierte das beim lokalen Testen scheinbar
klaglos; erst ``alembic upgrade head`` auf einer echten DB mit BEIDEN
Revisionen im Baum brach mit "Multiple head revisions are present" ab und
liess den Orchestrator gar nicht mehr hochkommen (Fallback auf
``create_all`` legt keine Spalten auf bestehenden Tabellen an). Umbenannt
auf eine tatsaechlich neu gezogene ID.

Zwei der vier ueberlappenden Autonomie/Berechtigungs-Systeme
(Autonomy-Matrix, Sudo-Pakete) lagen bisher als ad-hoc Schluessel im
Allzweck-Blob ``agent.config`` (``autonomy_matrix``/``permissions``/
``permissions_mode``) -- ein Nutzer, der wissen wollte "was darf dieser
Agent?", musste vier verschiedene Oberflaechen pruefen (Issue #787). Diese
Migration verschiebt die drei Schluessel in eine eigene, benannte Spalte
und fuehrt gleich den neuen, bisher gar nicht vorhandenen dauerhaften
Computer-Use-Default (vierter Schluessel) ein.

Reine Datenverschiebung, kein Verhaltenswechsel: ``normalize_matrix(None,
level)`` und ``effective_permissions()`` liefern vor und nach dem Backfill
dasselbe Ergebnis fuer jeden bestehenden Agenten.

``ADD COLUMN IF NOT EXISTS`` + reines ``op.execute`` ohne vorheriges SELECT
-- wie seit #689 in diesem Baum Konvention, wegen ``alembic upgrade head
--sql`` ohne DB-Verbindung.

``command_policies`` (drittes System) bleibt unangetastet: eigene Tabelle,
eigene Form (Liste von Regex-Regeln statt fixer Schluessel), wird ueber den
neuen Endpunkt nur mitgeliefert (Read-Through), nicht hierher verschoben.
"""
from alembic import op

revision = "4e7b040149c2"
down_revision = "7a9c2e4f1b3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS access_policy "
        "JSON NOT NULL DEFAULT '{}'"
    )
    # jsonb_strip_nulls: ein Agent, der z.B. nur permissions_mode gesetzt
    # hatte, soll nicht ploetzlich ein explizites autonomy_matrix:null tragen
    # -- das waere vom "nie gesetzt"-Fall unterscheidbar und wuerde
    # normalize_matrix()s Preset-Fallback durchkreuzen.
    op.execute(
        """
        UPDATE agents
        SET access_policy = (
            to_jsonb(access_policy) || jsonb_strip_nulls(jsonb_build_object(
                'autonomy_matrix', config->'autonomy_matrix',
                'permissions', config->'permissions',
                'permissions_mode', config->'permissions_mode'
            ))
        )::json
        WHERE config::jsonb ? 'autonomy_matrix' OR config::jsonb ? 'permissions'
           OR config::jsonb ? 'permissions_mode'
        """
    )
    # Alte Schluessel aus config entfernen -- sonst gibt es wieder zwei
    # Wahrheiten, genau das Problem, das diese Migration beheben soll.
    op.execute(
        """
        UPDATE agents
        SET config = (to_jsonb(config) - 'autonomy_matrix' - 'permissions'
                      - 'permissions_mode')::json
        WHERE config::jsonb ? 'autonomy_matrix' OR config::jsonb ? 'permissions'
           OR config::jsonb ? 'permissions_mode'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE agents
        SET config = (
            to_jsonb(config) || jsonb_strip_nulls(jsonb_build_object(
                'autonomy_matrix', access_policy->'autonomy_matrix',
                'permissions', access_policy->'permissions',
                'permissions_mode', access_policy->'permissions_mode'
            ))
        )::json
        """
    )
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS access_policy")
