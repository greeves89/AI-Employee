"""L3 ohne sudo als Vorgabe: bestehende L3-Agenten behalten "Pakete / Config"

Ab v1.406.0 erlaubt die Stufe L3 ``system_config`` nicht mehr von selbst (sudo im
Container ist root). Agenten, die es bisher über die Stufe hatten, sollen es
behalten: ``allow`` wird ausdrücklich in ihre Matrix geschrieben. Betrifft nur
Agenten auf L3 im automatischen Modus ohne eigenen gültigen Wert — von Hand
gesetzte Pakete und eigene Matrixwerte bleiben, wie sie sind.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-10-30
"""
from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE agents
        SET access_policy = jsonb_set(
            jsonb_set(
                COALESCE(access_policy::jsonb, '{}'::jsonb),
                '{autonomy_matrix}',
                CASE WHEN jsonb_typeof(access_policy::jsonb -> 'autonomy_matrix') = 'object'
                     THEN access_policy::jsonb -> 'autonomy_matrix'
                     ELSE '{}'::jsonb END,
                true),
            '{autonomy_matrix,system_config}', '"allow"'::jsonb, true)::json
        WHERE lower(COALESCE(autonomy_level, 'l3')) = 'l3'
          AND COALESCE(access_policy::jsonb ->> 'permissions_mode', '') <> 'manual'
          AND COALESCE(access_policy::jsonb -> 'autonomy_matrix' ->> 'system_config', '')
              NOT IN ('allow', 'ask', 'deny')
        """
    )


def downgrade() -> None:
    # Nicht umkehrbar: danach ist nicht mehr zu unterscheiden, wer den Wert selbst
    # gesetzt hat. Er schadet auch nicht — er beschreibt den Stand vor dem Upgrade.
    pass
