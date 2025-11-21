"""
Forum Models for Discussion Forum System
"""
from datetime import datetime
from sqlalchemy import Index
from app.extensions import db


class ForumPost(db.Model):
    """Main forum post model"""
    __tablename__ = 'forum_post'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text, nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Admin controls
    is_locked = db.Column(db.Boolean, default=False)
    is_featured = db.Column(db.Boolean, default=False)
    is_highlighted = db.Column(db.Boolean, default=False)
    
    # Relationships
    author = db.relationship('User', backref=db.backref('forum_posts', lazy=True))
    files = db.relationship('ForumFile', backref='post', lazy=True, cascade='all, delete-orphan')
    links = db.relationship('ForumLink', backref='post', lazy=True, cascade='all, delete-orphan')
    comments = db.relationship('ForumComment', backref='post', lazy=True, cascade='all, delete-orphan', order_by='ForumComment.created_at')
    reactions = db.relationship('ForumReaction', backref='post', lazy=True, cascade='all, delete-orphan', foreign_keys='ForumReaction.post_id')
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_forum_post_created_at', 'created_at'),
        Index('idx_forum_post_author_id', 'author_id'),
        Index('idx_forum_post_slug', 'slug'),
    )
    
    def __repr__(self):
        return f'<ForumPost {self.id}: {self.title}>'
    
    @property
    def like_count(self):
        return ForumReaction.query.filter_by(post_id=self.id, reaction_type='like').count()
    
    @property
    def dislike_count(self):
        return ForumReaction.query.filter_by(post_id=self.id, reaction_type='dislike').count()
    
    @property
    def comment_count(self):
        return len(self.comments)


class ForumFile(db.Model):
    """Files attached to forum posts"""
    __tablename__ = 'forum_file'
    
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('forum_post.id'), nullable=False)
    file_url = db.Column(db.String(512), nullable=False)
    public_id = db.Column(db.String(255))  # Cloudinary public_id for deletion
    filename = db.Column(db.String(255))
    file_type = db.Column(db.String(50))  # image, pdf, doc, etc.
    file_size = db.Column(db.Integer)  # Size in bytes
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<ForumFile {self.id}: {self.filename}>'


class ForumLink(db.Model):
    """External links in forum posts"""
    __tablename__ = 'forum_link'
    
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('forum_post.id'), nullable=False)
    url = db.Column(db.String(512), nullable=False)
    title = db.Column(db.String(255))  # Optional title for the link
    link_type = db.Column(db.String(50))  # youtube, github, blog, etc.
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<ForumLink {self.id}: {self.url}>'


class ForumComment(db.Model):
    """Comments on forum posts"""
    __tablename__ = 'forum_comment'
    
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('forum_post.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    body = db.Column(db.Text, nullable=False)
    file_url = db.Column(db.String(512))  # Optional file attachment
    public_id = db.Column(db.String(255))  # Cloudinary public_id for deletion
    filename = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    author = db.relationship('User', backref=db.backref('forum_comments', lazy=True))
    reactions = db.relationship('ForumReaction', backref='comment', lazy=True, cascade='all, delete-orphan', foreign_keys='ForumReaction.comment_id')
    
    # Indexes
    __table_args__ = (
        Index('idx_forum_comment_post_id', 'post_id'),
        Index('idx_forum_comment_author_id', 'author_id'),
        Index('idx_forum_comment_created_at', 'created_at'),
    )
    
    def __repr__(self):
        return f'<ForumComment {self.id} on post {self.post_id}>'
    
    @property
    def like_count(self):
        return ForumReaction.query.filter_by(comment_id=self.id, reaction_type='like').count()
    
    @property
    def dislike_count(self):
        return ForumReaction.query.filter_by(comment_id=self.id, reaction_type='dislike').count()


class ForumReaction(db.Model):
    """Likes and dislikes for posts and comments"""
    __tablename__ = 'forum_reaction'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('forum_post.id'), nullable=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('forum_comment.id'), nullable=True)
    reaction_type = db.Column(db.String(20), nullable=False)  # 'like' or 'dislike'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('forum_reactions', lazy=True))
    
    # Indexes and constraints
    __table_args__ = (
        Index('idx_forum_reaction_user_post', 'user_id', 'post_id'),
        Index('idx_forum_reaction_user_comment', 'user_id', 'comment_id'),
        db.UniqueConstraint('user_id', 'post_id', name='uq_user_post_reaction'),
        db.UniqueConstraint('user_id', 'comment_id', name='uq_user_comment_reaction'),
    )
    
    def __repr__(self):
        return f'<ForumReaction {self.id}: {self.reaction_type}>'


class ForumBan(db.Model):
    """Banned users from posting in forum"""
    __tablename__ = 'forum_ban'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    banned_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.Text)
    banned_at = db.Column(db.DateTime, default=datetime.utcnow)
    unbanned_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('forum_ban', uselist=False))
    banned_by = db.relationship('User', foreign_keys=[banned_by_id])
    
    def __repr__(self):
        return f'<ForumBan {self.id}: user {self.user_id}>'

