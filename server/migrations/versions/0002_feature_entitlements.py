"""Feature entitlements: module grants (AKENO VPN, AKENO LAN Play …) next to the suite license.

Additive: two new tables and new columns. Nothing existing is rewritten except that offline certificates learn
their account (``user_id``, copied from their license). Every existing license, code, redemption, device,
certificate and audit entry keeps its values; existing codes stay suite codes (``entitlement = akeno-suite``).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29 08:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('feature_grants',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('entitlement', sa.String(length=40), nullable=False),
    sa.Column('grant_type', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('device_limit', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("(grant_type = 'lifetime') = (expires_at IS NULL)", name='ck_feature_grants_lifetime_expiry'),
    sa.CheckConstraint("grant_type in ('lifetime','timed')", name='ck_feature_grants_type'),
    sa.CheckConstraint("status in ('active','disabled','revoked')", name='ck_feature_grants_status'),
    sa.CheckConstraint('device_limit >= 1', name='ck_feature_grants_device_limit'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'entitlement', name='uq_feature_grants_user_entitlement')
    )
    with op.batch_alter_table('feature_grants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_feature_grants_user_id'), ['user_id'], unique=False)

    op.create_table('feature_grant_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('grant_id', sa.Uuid(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('old_type', sa.String(length=20), nullable=True),
    sa.Column('new_type', sa.String(length=20), nullable=True),
    sa.Column('old_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('new_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('code_id', sa.Uuid(), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['code_id'], ['license_codes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['grant_id'], ['feature_grants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('feature_grant_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_feature_grant_events_grant_id'), ['grant_id'], unique=False)

    with op.batch_alter_table('license_codes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('entitlement', sa.String(length=40), server_default='akeno-suite',
                                      nullable=False))
        batch_op.add_column(sa.Column('duration_days', sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            'ck_codes_duration_days', 'duration_days IS NULL OR (duration_days >= 1 AND duration_days <= 3650)')

    with op.batch_alter_table('license_redemptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('grant_id', sa.Uuid(), nullable=True))
        batch_op.alter_column('license_id', existing_type=sa.Uuid(), nullable=True)
        batch_op.create_foreign_key('fk_license_redemptions_grant_id', 'feature_grants', ['grant_id'], ['id'],
                                    ondelete='CASCADE')

    with op.batch_alter_table('offline_certificates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('entitlements', sa.JSON(), nullable=True))
        batch_op.alter_column('license_id', existing_type=sa.Uuid(), nullable=True)
        batch_op.create_index(batch_op.f('ix_offline_certificates_user_id'), ['user_id'], unique=False)
        batch_op.create_foreign_key('fk_offline_certificates_user_id', 'users', ['user_id'], ['id'],
                                    ondelete='CASCADE')
    # the account of every existing certificate is the account of its license
    op.execute("UPDATE offline_certificates SET user_id = "
               "(SELECT licenses.user_id FROM licenses WHERE licenses.id = offline_certificates.license_id) "
               "WHERE user_id IS NULL")


def downgrade() -> None:
    # Back to suite-only: module grants and their history go; module codes that were never used are withdrawn
    # (as suite codes they would unlock AKENO Full); records that only exist for module grants are removed.
    op.execute("UPDATE license_codes SET status = 'revoked' WHERE entitlement <> 'akeno-suite' "
               "AND status IN ('available', 'reserved')")
    op.execute("DELETE FROM license_redemptions WHERE license_id IS NULL")
    op.execute("DELETE FROM offline_certificates WHERE license_id IS NULL")
    with op.batch_alter_table('offline_certificates', schema=None) as batch_op:
        batch_op.drop_constraint('fk_offline_certificates_user_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_offline_certificates_user_id'))
        batch_op.alter_column('license_id', existing_type=sa.Uuid(), nullable=False)
        batch_op.drop_column('entitlements')
        batch_op.drop_column('user_id')

    with op.batch_alter_table('license_redemptions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_license_redemptions_grant_id', type_='foreignkey')
        batch_op.alter_column('license_id', existing_type=sa.Uuid(), nullable=False)
        batch_op.drop_column('grant_id')

    with op.batch_alter_table('license_codes', schema=None) as batch_op:
        batch_op.drop_constraint('ck_codes_duration_days', type_='check')
        batch_op.drop_column('duration_days')
        batch_op.drop_column('entitlement')

    with op.batch_alter_table('feature_grant_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_feature_grant_events_grant_id'))
    op.drop_table('feature_grant_events')
    with op.batch_alter_table('feature_grants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_feature_grants_user_id'))
    op.drop_table('feature_grants')
