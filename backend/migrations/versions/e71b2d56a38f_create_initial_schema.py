"""create initial schema

Revision ID: e71b2d56a38f
Revises: 
Create Date: 2026-08-29 22:56:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e71b2d56a38f'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create stores table
    op.create_table(
        'stores',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('location', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_stores_id'), 'stores', ['id'], unique=False)

    # 2. Create cameras table
    op.create_table(
        'cameras',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('store_id', sa.Integer(), nullable=False),
        sa.Column('aisle', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cameras_id'), 'cameras', ['id'], unique=False)

    # 3. Create captures table
    op.create_table(
        'captures',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('camera_id', sa.Integer(), nullable=False),
        sa.Column('image_url', sa.String(), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['camera_id'], ['cameras.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_captures_id'), 'captures', ['id'], unique=False)

    # 4. Create detections table
    op.create_table(
        'detections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('capture_id', sa.Integer(), nullable=False),
        sa.Column('class', sa.String(), nullable=False),
        sa.Column('bbox', sa.JSON(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(['capture_id'], ['captures.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_detections_id'), 'detections', ['id'], unique=False)

    # 5. Create shelf_reports table
    op.create_table(
        'shelf_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('capture_id', sa.Integer(), nullable=False),
        sa.Column('fill_percentage', sa.Float(), nullable=False),
        sa.Column('empty_regions_json', sa.JSON(), nullable=True),
        sa.Column('urgency', sa.String(), nullable=False),
        sa.Column('llm_summary', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['capture_id'], ['captures.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_shelf_reports_id'), 'shelf_reports', ['id'], unique=False)

    # 6. Create alerts table
    op.create_table(
        'alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('shelf_report_id', sa.Integer(), nullable=False),
        sa.Column('sent_to_slack', sa.Boolean(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['shelf_report_id'], ['shelf_reports.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_alerts_id'), 'alerts', ['id'], unique=False)

    # 7. Create products table (added in previous phase)
    op.create_table(
        'products',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('upc', sa.String(), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('expected_quantity', sa.Integer(), nullable=False),
        sa.Column('current_quantity', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_products_id'), 'products', ['id'], unique=False)
    op.create_index(op.f('ix_products_upc'), 'products', ['upc'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_products_upc'), table_name='products')
    op.drop_index(op.f('ix_products_id'), table_name='products')
    op.drop_table('products')
    op.drop_index(op.f('ix_alerts_id'), table_name='alerts')
    op.drop_table('alerts')
    op.drop_index(op.f('ix_shelf_reports_id'), table_name='shelf_reports')
    op.drop_table('shelf_reports')
    op.drop_index(op.f('ix_detections_id'), table_name='detections')
    op.drop_table('detections')
    op.drop_index(op.f('ix_captures_id'), table_name='captures')
    op.drop_table('captures')
    op.drop_index(op.f('ix_cameras_id'), table_name='cameras')
    op.drop_table('cameras')
    op.drop_index(op.f('ix_stores_id'), table_name='stores')
    op.drop_table('stores')
