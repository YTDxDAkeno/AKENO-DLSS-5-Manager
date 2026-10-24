"""Licensing schema: accounts, sessions, devices, licenses, codes, certificates, audit, settings.

Revision ID: 0001
Revises: 
Create Date: 2026-10-23 22:38:03.907515
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('signing_keys',
    sa.Column('kid', sa.String(length=32), nullable=False),
    sa.Column('public_key', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('endorsement', sa.Text(), nullable=True),
    sa.Column('endorsed_by', sa.String(length=32), nullable=True),
    sa.PrimaryKeyConstraint('kid')
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('email_normalized', sa.String(length=320), nullable=False),
    sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('display_name', sa.String(length=80), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('disabled_reason', sa.String(length=300), nullable=False),
    sa.Column('language', sa.String(length=5), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failed_logins', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("role in ('user','support','admin','super_admin')", name='ck_users_role'),
    sa.CheckConstraint("status in ('active','disabled')", name='ck_users_status'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email_normalized')
    )
    op.create_table('admin_audit_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('actor_email', sa.String(length=320), nullable=False),
    sa.Column('actor_role', sa.String(length=20), nullable=False),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('target_type', sa.String(length=30), nullable=False),
    sa.Column('target_id', sa.String(length=64), nullable=False),
    sa.Column('ip', sa.String(length=45), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('admin_audit_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_admin_audit_events_action'), ['action'], unique=False)
        batch_op.create_index(batch_op.f('ix_admin_audit_events_actor_id'), ['actor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_admin_audit_events_at'), ['at'], unique=False)
        batch_op.create_index(batch_op.f('ix_admin_audit_events_target_id'), ['target_id'], unique=False)

    op.create_table('devices',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('os', sa.String(length=80), nullable=False),
    sa.Column('app_version', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by', sa.String(length=20), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'fingerprint', name='uq_devices_user_fingerprint')
    )
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_devices_user_id'), ['user_id'], unique=False)

    op.create_table('email_verification_tokens',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('email_verification_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_verification_tokens_user_id'), ['user_id'], unique=False)

    op.create_table('license_codes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('verifier', sa.String(length=64), nullable=False),
    sa.Column('hint', sa.String(length=12), nullable=False),
    sa.Column('code_type', sa.String(length=20), nullable=False),
    sa.Column('device_allowance', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=False),
    sa.Column('batch', sa.String(length=80), nullable=False),
    sa.Column('assigned_user_id', sa.Uuid(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reserved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reserved_for', sa.String(length=160), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by', sa.Uuid(), nullable=True),
    sa.Column('redeemed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('redeemed_by', sa.Uuid(), nullable=True),
    sa.CheckConstraint("code_type in ('trial_24h','trial_72h','monthly','lifetime')", name='ck_codes_type'),
    sa.CheckConstraint("status in ('available','reserved','redeemed','revoked')", name='ck_codes_status'),
    sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['redeemed_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['revoked_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('verifier')
    )
    with op.batch_alter_table('license_codes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_license_codes_batch'), ['batch'], unique=False)
        batch_op.create_index(batch_op.f('ix_license_codes_redeemed_by'), ['redeemed_by'], unique=False)
        batch_op.create_index('ix_license_codes_status_created', ['status', 'created_at'], unique=False)

    op.create_table('licenses',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('license_type', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('device_limit', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("(license_type = 'lifetime') = (expires_at IS NULL)", name='ck_licenses_lifetime_expiry'),
    sa.CheckConstraint("license_type in ('trial_24h','trial_72h','monthly','lifetime')", name='ck_licenses_type'),
    sa.CheckConstraint("status in ('active','disabled','revoked')", name='ck_licenses_status'),
    sa.CheckConstraint('device_limit >= 1', name='ck_licenses_device_limit'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('password_reset_tokens',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('password_reset_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_password_reset_tokens_user_id'), ['user_id'], unique=False)

    op.create_table('server_settings',
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('sign_in_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('reason', sa.String(length=40), nullable=False),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('ip', sa.String(length=45), nullable=False),
    sa.Column('user_agent', sa.String(length=300), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sign_in_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sign_in_events_at'), ['at'], unique=False)
        batch_op.create_index(batch_op.f('ix_sign_in_events_user_id'), ['user_id'], unique=False)

    op.create_table('user_credentials',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('totp_secret_enc', sa.String(length=255), nullable=True),
    sa.Column('totp_enabled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('totp_last_step', sa.BigInteger(), nullable=True),
    sa.Column('recovery_codes', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('license_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('license_id', sa.Uuid(), nullable=False),
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
    sa.ForeignKeyConstraint(['license_id'], ['licenses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('license_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_license_events_license_id'), ['license_id'], unique=False)

    op.create_table('license_redemptions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('code_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('license_id', sa.Uuid(), nullable=False),
    sa.Column('redeemed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('previous_type', sa.String(length=20), nullable=True),
    sa.Column('previous_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('new_type', sa.String(length=20), nullable=False),
    sa.Column('new_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('ip', sa.String(length=45), nullable=False),
    sa.ForeignKeyConstraint(['code_id'], ['license_codes.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['license_id'], ['licenses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_id')
    )
    with op.batch_alter_table('license_redemptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_license_redemptions_user_id'), ['user_id'], unique=False)

    op.create_table('license_validations',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('license_id', sa.Uuid(), nullable=True),
    sa.Column('device_id', sa.Uuid(), nullable=True),
    sa.Column('result', sa.String(length=30), nullable=False),
    sa.Column('app_version', sa.String(length=40), nullable=False),
    sa.Column('ip', sa.String(length=45), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['license_id'], ['licenses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('license_validations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_license_validations_at'), ['at'], unique=False)
        batch_op.create_index(batch_op.f('ix_license_validations_user_id'), ['user_id'], unique=False)

    op.create_table('offline_certificates',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('license_id', sa.Uuid(), nullable=False),
    sa.Column('device_id', sa.Uuid(), nullable=False),
    sa.Column('kid', sa.String(length=32), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoke_reason', sa.String(length=60), nullable=False),
    sa.Column('payload_sha256', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['license_id'], ['licenses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('offline_certificates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_offline_certificates_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_offline_certificates_license_id'), ['license_id'], unique=False)

    op.create_table('user_sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('prev_token_hash', sa.String(length=64), nullable=True),
    sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoke_reason', sa.String(length=60), nullable=False),
    sa.Column('mfa_verified', sa.Boolean(), nullable=False),
    sa.Column('csrf_token', sa.String(length=64), nullable=False),
    sa.Column('ip', sa.String(length=45), nullable=False),
    sa.Column('user_agent', sa.String(length=300), nullable=False),
    sa.Column('device_id', sa.Uuid(), nullable=True),
    sa.CheckConstraint("kind in ('app','web')", name='ck_sessions_kind'),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_sessions_prev_token_hash'), ['prev_token_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_sessions_user_id'), ['user_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_sessions_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_sessions_prev_token_hash'))

    op.drop_table('user_sessions')
    with op.batch_alter_table('offline_certificates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_offline_certificates_license_id'))
        batch_op.drop_index(batch_op.f('ix_offline_certificates_device_id'))

    op.drop_table('offline_certificates')
    with op.batch_alter_table('license_validations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_license_validations_user_id'))
        batch_op.drop_index(batch_op.f('ix_license_validations_at'))

    op.drop_table('license_validations')
    with op.batch_alter_table('license_redemptions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_license_redemptions_user_id'))

    op.drop_table('license_redemptions')
    with op.batch_alter_table('license_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_license_events_license_id'))

    op.drop_table('license_events')
    op.drop_table('user_credentials')
    with op.batch_alter_table('sign_in_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sign_in_events_user_id'))
        batch_op.drop_index(batch_op.f('ix_sign_in_events_at'))

    op.drop_table('sign_in_events')
    op.drop_table('server_settings')
    with op.batch_alter_table('password_reset_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_password_reset_tokens_user_id'))

    op.drop_table('password_reset_tokens')
    op.drop_table('licenses')
    with op.batch_alter_table('license_codes', schema=None) as batch_op:
        batch_op.drop_index('ix_license_codes_status_created')
        batch_op.drop_index(batch_op.f('ix_license_codes_redeemed_by'))
        batch_op.drop_index(batch_op.f('ix_license_codes_batch'))

    op.drop_table('license_codes')
    with op.batch_alter_table('email_verification_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_verification_tokens_user_id'))

    op.drop_table('email_verification_tokens')
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_devices_user_id'))

    op.drop_table('devices')
    with op.batch_alter_table('admin_audit_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_admin_audit_events_target_id'))
        batch_op.drop_index(batch_op.f('ix_admin_audit_events_at'))
        batch_op.drop_index(batch_op.f('ix_admin_audit_events_actor_id'))
        batch_op.drop_index(batch_op.f('ix_admin_audit_events_action'))

    op.drop_table('admin_audit_events')
    op.drop_table('users')
    op.drop_table('signing_keys')
