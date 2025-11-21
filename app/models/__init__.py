# Import all models for Alembic to detect them
from app import db
from app.models.forum import (
    ForumPost, ForumFile, ForumLink, 
    ForumComment, ForumReaction, ForumBan
)

__all__ = [
    'ForumPost', 'ForumFile', 'ForumLink',
    'ForumComment', 'ForumReaction', 'ForumBan'
]
