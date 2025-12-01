# Import all models for Alembic to detect them
from app import db
from app.models.forum import (
    ForumPost, ForumFile, ForumLink, 
    ForumComment, ForumReaction, ForumBan
)
from app.models.country import Country
from app.models.currency_rate import CurrencyRate
from app.shipping.models import ShippingMode, ShippingRule

__all__ = [
    'ForumPost', 'ForumFile', 'ForumLink',
    'ForumComment', 'ForumReaction', 'ForumBan',
    'Country',
    'CurrencyRate',
    'ShippingMode',
    'ShippingRule'
]
