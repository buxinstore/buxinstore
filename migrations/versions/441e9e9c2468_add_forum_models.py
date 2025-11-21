"""add_forum_models

Revision ID: 441e9e9c2468
Revises: e33f4a5b6789
Create Date: 2025-01-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '441e9e9c2468'
down_revision: Union[str, None] = 'e33f4a5b6789'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create forum_post table
    op.create_table(
        'forum_post',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('slug', sa.String(length=255), nullable=False),
        sa.Column('author_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('is_locked', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('is_featured', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('is_highlighted', sa.Boolean(), nullable=True, server_default='0'),
        sa.ForeignKeyConstraint(['author_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_forum_post_author_id', 'forum_post', ['author_id'], unique=False)
    op.create_index('idx_forum_post_created_at', 'forum_post', ['created_at'], unique=False)
    op.create_index('idx_forum_post_slug', 'forum_post', ['slug'], unique=True)

    # Create forum_file table
    op.create_table(
        'forum_file',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('file_url', sa.String(length=512), nullable=False),
        sa.Column('public_id', sa.String(length=255), nullable=True),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('file_type', sa.String(length=50), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['post_id'], ['forum_post.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create forum_link table
    op.create_table(
        'forum_link',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('url', sa.String(length=512), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('link_type', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['post_id'], ['forum_post.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create forum_comment table
    op.create_table(
        'forum_comment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('author_id', sa.Integer(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('file_url', sa.String(length=512), nullable=True),
        sa.Column('public_id', sa.String(length=255), nullable=True),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['author_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['post_id'], ['forum_post.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_forum_comment_author_id', 'forum_comment', ['author_id'], unique=False)
    op.create_index('idx_forum_comment_created_at', 'forum_comment', ['created_at'], unique=False)
    op.create_index('idx_forum_comment_post_id', 'forum_comment', ['post_id'], unique=False)

    # Create forum_reaction table
    op.create_table(
        'forum_reaction',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=True),
        sa.Column('comment_id', sa.Integer(), nullable=True),
        sa.Column('reaction_type', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['comment_id'], ['forum_comment.id'], ),
        sa.ForeignKeyConstraint(['post_id'], ['forum_post.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'post_id', name='uq_user_post_reaction'),
        sa.UniqueConstraint('user_id', 'comment_id', name='uq_user_comment_reaction')
    )
    op.create_index('idx_forum_reaction_user_comment', 'forum_reaction', ['user_id', 'comment_id'], unique=False)
    op.create_index('idx_forum_reaction_user_post', 'forum_reaction', ['user_id', 'post_id'], unique=False)

    # Create forum_ban table
    op.create_table(
        'forum_ban',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('banned_by_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('banned_at', sa.DateTime(), nullable=True),
        sa.Column('unbanned_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True, server_default='1'),
        sa.ForeignKeyConstraint(['banned_by_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table('forum_ban')
    op.drop_index('idx_forum_reaction_user_post', table_name='forum_reaction')
    op.drop_index('idx_forum_reaction_user_comment', table_name='forum_reaction')
    op.drop_table('forum_reaction')
    op.drop_index('idx_forum_comment_post_id', table_name='forum_comment')
    op.drop_index('idx_forum_comment_created_at', table_name='forum_comment')
    op.drop_index('idx_forum_comment_author_id', table_name='forum_comment')
    op.drop_table('forum_comment')
    op.drop_table('forum_link')
    op.drop_table('forum_file')
    op.drop_index('idx_forum_post_slug', table_name='forum_post')
    op.drop_index('idx_forum_post_created_at', table_name='forum_post')
    op.drop_index('idx_forum_post_author_id', table_name='forum_post')
    op.drop_table('forum_post')

