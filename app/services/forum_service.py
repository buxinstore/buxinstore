"""
Forum Service - Business logic for forum operations
"""
import re
import os
from datetime import datetime
from typing import List, Optional, Dict, Tuple
from werkzeug.utils import secure_filename
from flask import current_app
from app.extensions import db
from app.models.forum import (
    ForumPost, ForumFile, ForumLink, ForumComment, 
    ForumReaction, ForumBan
)
from app.utils.cloudinary_utils import (
    upload_to_cloudinary, delete_from_cloudinary,
    get_allowed_extensions, get_resource_type,
    get_public_id_from_url
)


# Allowed file extensions for forum (no videos)
FORUM_ALLOWED_EXTENSIONS = {
    'jpg', 'jpeg', 'png', 'gif', 'webp',  # Images
    'pdf', 'doc', 'docx', 'txt', 'zip', 'rar',  # Documents
    'xls', 'xlsx', 'ppt', 'pptx',  # Office files
}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_FILES_PER_POST = 5


def generate_slug(title: str) -> str:
    """Generate a URL-friendly slug from title"""
    # Convert to lowercase
    slug = title.lower()
    # Replace spaces and special chars with hyphens
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[-\s]+', '-', slug)
    # Remove leading/trailing hyphens
    slug = slug.strip('-')
    # Limit length
    if len(slug) > 200:
        slug = slug[:200]
    return slug


def ensure_unique_slug(base_slug: str, exclude_post_id: Optional[int] = None) -> str:
    """Ensure slug is unique by appending number if needed"""
    slug = base_slug
    counter = 1
    
    while True:
        query = ForumPost.query.filter_by(slug=slug)
        if exclude_post_id:
            query = query.filter(ForumPost.id != exclude_post_id)
        
        if not query.first():
            return slug
        
        slug = f"{base_slug}-{counter}"
        counter += 1


def validate_file(file) -> Tuple[bool, Optional[str]]:
    """Validate uploaded file"""
    if not file or not hasattr(file, 'filename') or not file.filename:
        return False, "No file provided"
    
    # Check extension
    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    
    if ext not in FORUM_ALLOWED_EXTENSIONS:
        return False, f"File type '{ext}' not allowed. Allowed types: {', '.join(sorted(FORUM_ALLOWED_EXTENSIONS))}"
    
    # Check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    if file_size > MAX_FILE_SIZE:
        return False, f"File size ({file_size / 1024 / 1024:.2f} MB) exceeds maximum allowed size (10 MB)"
    
    # Check if it's a video (double check)
    if ext in {'mp4', 'mov', 'avi', 'mkv', 'webm'}:
        return False, "Video files are not allowed"
    
    return True, None


def upload_forum_file(file, folder: str = 'forum') -> Optional[Dict]:
    """Upload a file to Cloudinary for forum"""
    try:
        # Validate file
        is_valid, error_msg = validate_file(file)
        if not is_valid:
            current_app.logger.error(f"File validation failed: {error_msg}")
            return None
        
        # Upload to Cloudinary
        result = upload_to_cloudinary(file, folder=folder, resource_type='auto')
        
        if not result:
            return None
        
        # Get file info
        filename = secure_filename(file.filename)
        ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
        file_type = 'image' if ext in {'jpg', 'jpeg', 'png', 'gif', 'webp'} else 'document'
        
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        return {
            'url': result['url'],
            'public_id': result.get('public_id'),
            'filename': filename,
            'file_type': file_type,
            'file_size': file_size
        }
    except Exception as e:
        current_app.logger.error(f"Error uploading forum file: {str(e)}")
        return None


def detect_link_type(url: str) -> str:
    """Detect the type of external link"""
    url_lower = url.lower()
    
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'github.com' in url_lower:
        return 'github'
    elif 'blog' in url_lower or 'medium.com' in url_lower or 'dev.to' in url_lower:
        return 'blog'
    elif 'stackoverflow.com' in url_lower or 'stackexchange.com' in url_lower:
        return 'stackoverflow'
    else:
        return 'link'


def create_post(
    title: str,
    body: str,
    author_id: int,
    files: Optional[List] = None,
    links: Optional[List[str]] = None
) -> Tuple[Optional[ForumPost], Optional[str]]:
    """Create a new forum post"""
    try:
        # Validate title and body
        if not title or not title.strip():
            return None, "Title is required"
        if not body or not body.strip():
            return None, "Body is required"
        
        # Generate unique slug
        base_slug = generate_slug(title)
        slug = ensure_unique_slug(base_slug)
        
        # Create post
        post = ForumPost(
            title=title.strip(),
            body=body.strip(),
            slug=slug,
            author_id=author_id
        )
        db.session.add(post)
        db.session.flush()  # Get post ID
        
        # Handle file uploads
        if files:
            file_count = 0
            for file in files:
                if file_count >= MAX_FILES_PER_POST:
                    break
                
                file_data = upload_forum_file(file, folder='forum/posts')
                if file_data:
                    forum_file = ForumFile(
                        post_id=post.id,
                        file_url=file_data['url'],
                        public_id=file_data.get('public_id'),
                        filename=file_data['filename'],
                        file_type=file_data['file_type'],
                        file_size=file_data['file_size']
                    )
                    db.session.add(forum_file)
                    file_count += 1
        
        # Handle external links
        if links:
            for link_url in links:
                if link_url and link_url.strip():
                    link_type = detect_link_type(link_url.strip())
                    forum_link = ForumLink(
                        post_id=post.id,
                        url=link_url.strip(),
                        link_type=link_type
                    )
                    db.session.add(forum_link)
        
        db.session.commit()
        return post, None
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating forum post: {str(e)}")
        return None, f"Error creating post: {str(e)}"


def update_post(
    post_id: int,
    title: str,
    body: str,
    author_id: int,
    files: Optional[List] = None,
    links: Optional[List[str]] = None
) -> Tuple[Optional[ForumPost], Optional[str]]:
    """Update an existing forum post"""
    try:
        post = ForumPost.query.get_or_404(post_id)
        
        # Check authorization
        if post.author_id != author_id:
            return None, "You don't have permission to edit this post"
        
        # Validate
        if not title or not title.strip():
            return None, "Title is required"
        if not body or not body.strip():
            return None, "Body is required"
        
        # Update title and body
        post.title = title.strip()
        post.body = body.strip()
        
        # Update slug if title changed
        new_slug = generate_slug(title.strip())
        if new_slug != post.slug:
            post.slug = ensure_unique_slug(new_slug, exclude_post_id=post.id)
        
        # Handle new file uploads (add to existing)
        existing_file_count = len(post.files)
        if files and existing_file_count < MAX_FILES_PER_POST:
            for file in files:
                if existing_file_count >= MAX_FILES_PER_POST:
                    break
                
                file_data = upload_forum_file(file, folder='forum/posts')
                if file_data:
                    forum_file = ForumFile(
                        post_id=post.id,
                        file_url=file_data['url'],
                        public_id=file_data.get('public_id'),
                        filename=file_data['filename'],
                        file_type=file_data['file_type'],
                        file_size=file_data['file_size']
                    )
                    db.session.add(forum_file)
                    existing_file_count += 1
        
        # Update links (replace all)
        if links is not None:
            # Delete existing links
            ForumLink.query.filter_by(post_id=post.id).delete()
            
            # Add new links
            for link_url in links:
                if link_url and link_url.strip():
                    link_type = detect_link_type(link_url.strip())
                    forum_link = ForumLink(
                        post_id=post.id,
                        url=link_url.strip(),
                        link_type=link_type
                    )
                    db.session.add(forum_link)
        
        post.updated_at = datetime.utcnow()
        db.session.commit()
        return post, None
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating forum post: {str(e)}")
        return None, f"Error updating post: {str(e)}"


def delete_post(post_id: int, user_id: int, is_admin: bool = False) -> Tuple[bool, Optional[str]]:
    """Delete a forum post"""
    try:
        post = ForumPost.query.get_or_404(post_id)
        
        # Check authorization
        if not is_admin and post.author_id != user_id:
            return False, "You don't have permission to delete this post"
        
        # Delete files from Cloudinary
        for file in post.files:
            if file.public_id:
                try:
                    delete_from_cloudinary(file.public_id, resource_type='auto')
                except Exception as e:
                    current_app.logger.warning(f"Failed to delete file from Cloudinary: {e}")
        
        # Delete comments' files
        for comment in post.comments:
            if comment.public_id:
                try:
                    delete_from_cloudinary(comment.public_id, resource_type='auto')
                except Exception as e:
                    current_app.logger.warning(f"Failed to delete comment file from Cloudinary: {e}")
        
        # Delete post (cascade will handle related records)
        db.session.delete(post)
        db.session.commit()
        return True, None
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting forum post: {str(e)}")
        return False, f"Error deleting post: {str(e)}"


def create_comment(
    post_id: int,
    body: str,
    author_id: int,
    file: Optional = None
) -> Tuple[Optional[ForumComment], Optional[str]]:
    """Create a comment on a forum post"""
    try:
        post = ForumPost.query.get_or_404(post_id)
        
        # Check if post is locked
        if post.is_locked:
            return None, "This post is locked and no longer accepts comments"
        
        # Validate
        if not body or not body.strip():
            return None, "Comment body is required"
        
        comment = ForumComment(
            post_id=post_id,
            body=body.strip(),
            author_id=author_id
        )
        db.session.add(comment)
        db.session.flush()
        
        # Handle file upload if provided
        if file:
            file_data = upload_forum_file(file, folder='forum/comments')
            if file_data:
                comment.file_url = file_data['url']
                comment.public_id = file_data.get('public_id')
                comment.filename = file_data['filename']
        
        db.session.commit()
        return comment, None
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating comment: {str(e)}")
        return None, f"Error creating comment: {str(e)}"


def delete_comment(comment_id: int, user_id: int, is_admin: bool = False) -> Tuple[bool, Optional[str]]:
    """Delete a comment"""
    try:
        comment = ForumComment.query.get_or_404(comment_id)
        
        # Check authorization
        if not is_admin and comment.author_id != user_id:
            return False, "You don't have permission to delete this comment"
        
        # Delete file from Cloudinary if exists
        if comment.public_id:
            try:
                delete_from_cloudinary(comment.public_id, resource_type='auto')
            except Exception as e:
                current_app.logger.warning(f"Failed to delete comment file from Cloudinary: {e}")
        
        db.session.delete(comment)
        db.session.commit()
        return True, None
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting comment: {str(e)}")
        return False, f"Error deleting comment: {str(e)}"


def toggle_reaction(
    user_id: int,
    post_id: Optional[int] = None,
    comment_id: Optional[int] = None,
    reaction_type: str = 'like'
) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """Toggle like/dislike on a post or comment"""
    try:
        if reaction_type not in ['like', 'dislike']:
            return False, "Invalid reaction type", None
        
        if not post_id and not comment_id:
            return False, "Either post_id or comment_id is required", None
        
        if post_id and comment_id:
            return False, "Cannot react to both post and comment", None
        
        # Check if reaction already exists
        if post_id:
            existing = ForumReaction.query.filter_by(
                user_id=user_id,
                post_id=post_id
            ).first()
        else:
            existing = ForumReaction.query.filter_by(
                user_id=user_id,
                comment_id=comment_id
            ).first()
        
        if existing:
            # If same reaction type, remove it (toggle off)
            if existing.reaction_type == reaction_type:
                db.session.delete(existing)
                action = 'removed'
            else:
                # Change reaction type
                existing.reaction_type = reaction_type
                action = 'changed'
        else:
            # Create new reaction
            reaction = ForumReaction(
                user_id=user_id,
                post_id=post_id,
                comment_id=comment_id,
                reaction_type=reaction_type
            )
            db.session.add(reaction)
            action = 'added'
        
        db.session.commit()
        
        # Get updated counts
        if post_id:
            like_count = ForumReaction.query.filter_by(post_id=post_id, reaction_type='like').count()
            dislike_count = ForumReaction.query.filter_by(post_id=post_id, reaction_type='dislike').count()
        else:
            like_count = ForumReaction.query.filter_by(comment_id=comment_id, reaction_type='like').count()
            dislike_count = ForumReaction.query.filter_by(comment_id=comment_id, reaction_type='dislike').count()
        
        return True, None, {
            'action': action,
            'reaction_type': reaction_type,
            'like_count': like_count,
            'dislike_count': dislike_count
        }
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error toggling reaction: {str(e)}")
        return False, f"Error toggling reaction: {str(e)}", None


def get_user_reaction(user_id: int, post_id: Optional[int] = None, comment_id: Optional[int] = None) -> Optional[str]:
    """Get user's reaction to a post or comment"""
    if post_id:
        reaction = ForumReaction.query.filter_by(user_id=user_id, post_id=post_id).first()
    elif comment_id:
        reaction = ForumReaction.query.filter_by(user_id=user_id, comment_id=comment_id).first()
    else:
        return None
    
    return reaction.reaction_type if reaction else None


def ban_user(user_id: int, banned_by_id: int, reason: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Ban a user from posting in forum"""
    try:
        # Check if already banned
        existing_ban = ForumBan.query.filter_by(user_id=user_id, is_active=True).first()
        if existing_ban:
            return False, "User is already banned"
        
        ban = ForumBan(
            user_id=user_id,
            banned_by_id=banned_by_id,
            reason=reason
        )
        db.session.add(ban)
        db.session.commit()
        return True, None
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error banning user: {str(e)}")
        return False, f"Error banning user: {str(e)}"


def unban_user(user_id: int) -> Tuple[bool, Optional[str]]:
    """Unban a user from forum"""
    try:
        ban = ForumBan.query.filter_by(user_id=user_id, is_active=True).first()
        if not ban:
            return False, "User is not banned"
        
        ban.is_active = False
        ban.unbanned_at = datetime.utcnow()
        db.session.commit()
        return True, None
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error unbanning user: {str(e)}")
        return False, f"Error unbanning user: {str(e)}"


def is_user_banned(user_id: int) -> bool:
    """Check if user is banned from forum"""
    ban = ForumBan.query.filter_by(user_id=user_id, is_active=True).first()
    return ban is not None


def delete_file_from_post(file_id: int, user_id: int, is_admin: bool = False) -> Tuple[bool, Optional[str]]:
    """Delete a file from a post (admin only or post author)"""
    try:
        file = ForumFile.query.get_or_404(file_id)
        post = file.post
        
        # Check authorization
        if not is_admin and post.author_id != user_id:
            return False, "You don't have permission to delete this file"
        
        # Delete from Cloudinary
        if file.public_id:
            try:
                delete_from_cloudinary(file.public_id, resource_type='auto')
            except Exception as e:
                current_app.logger.warning(f"Failed to delete file from Cloudinary: {e}")
        
        db.session.delete(file)
        db.session.commit()
        return True, None
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting file: {str(e)}")
        return False, f"Error deleting file: {str(e)}"

