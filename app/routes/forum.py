"""
Forum Routes Blueprint
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy import desc, or_
from app.extensions import db
from app.models.forum import ForumPost, ForumComment, ForumBan
from app.services.forum_service import (
    create_post, update_post, delete_post,
    create_comment, delete_comment,
    toggle_reaction, get_user_reaction,
    ban_user, unban_user, is_user_banned,
    delete_file_from_post
)
from functools import wraps

forum_bp = Blueprint('forum', __name__, url_prefix='/forum')


def admin_required(f):
    """Admin decorator for forum routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or (not current_user.is_admin and current_user.role != 'admin'):
            flash('Admin access required', 'error')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


@forum_bp.route('')
def index():
    """Forum index page - list all posts"""
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()
    sort = request.args.get('sort', 'newest')  # newest, oldest, most_comments, most_likes
    
    query = ForumPost.query
    
    # Search filter
    if search:
        query = query.filter(
            or_(
                ForumPost.title.ilike(f'%{search}%'),
                ForumPost.body.ilike(f'%{search}%')
            )
        )
    
    # Sorting
    if sort == 'oldest':
        query = query.order_by(ForumPost.created_at.asc())
    elif sort == 'most_comments':
        # This is a simplified version - in production you might want to use a subquery
        query = query.order_by(desc(ForumPost.created_at))
    elif sort == 'most_likes':
        query = query.order_by(desc(ForumPost.created_at))
    else:  # newest (default)
        query = query.order_by(desc(ForumPost.created_at))
    
    # Featured posts first
    query = query.order_by(desc(ForumPost.is_featured), desc(ForumPost.created_at))
    
    posts = query.paginate(page=page, per_page=20, error_out=False)
    
    # Get user reactions for all posts on this page
    user_reactions = {}
    if current_user.is_authenticated:
        for post in posts.items:
            reaction = get_user_reaction(current_user.id, post_id=post.id)
            if reaction:
                user_reactions[post.id] = reaction
    
    return render_template('forum/forum_index.html',
                         posts=posts,
                         search=search,
                         sort=sort,
                         user_reactions=user_reactions)


@forum_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    """Create a new forum post"""
    # Check if user is banned
    if is_user_banned(current_user.id):
        flash('You are banned from posting in the forum', 'error')
        return redirect(url_for('forum.index'))
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        body = request.form.get('body', '').strip()
        
        # Get files
        files = []
        if 'files' in request.files:
            file_list = request.files.getlist('files')
            files = [f for f in file_list if f.filename]
        
        # Get links
        links = []
        links_input = request.form.get('links', '').strip()
        if links_input:
            # Split by newline or comma
            links = [link.strip() for link in links_input.replace(',', '\n').split('\n') if link.strip()]
        
        post, error = create_post(
            title=title,
            body=body,
            author_id=current_user.id,
            files=files if files else None,
            links=links if links else None
        )
        
        if post:
            flash('Post created successfully!', 'success')
            return redirect(url_for('forum.post', slug=post.slug))
        else:
            flash(error or 'Error creating post', 'error')
    
    return render_template('forum/forum_create.html')


@forum_bp.route('/<slug>')
def post(slug):
    """View a single forum post"""
    post = ForumPost.query.filter_by(slug=slug).first_or_404()
    
    # Get user reaction
    user_reaction = None
    if current_user.is_authenticated:
        user_reaction = get_user_reaction(current_user.id, post_id=post.id)
    
    # Get user reactions for comments
    comment_reactions = {}
    if current_user.is_authenticated:
        for comment in post.comments:
            reaction = get_user_reaction(current_user.id, comment_id=comment.id)
            if reaction:
                comment_reactions[comment.id] = reaction
    
    return render_template('forum/forum_post.html',
                         post=post,
                         user_reaction=user_reaction,
                         comment_reactions=comment_reactions)


@forum_bp.route('/<slug>/edit', methods=['GET', 'POST'])
@login_required
def edit(slug):
    """Edit a forum post"""
    post = ForumPost.query.filter_by(slug=slug).first_or_404()
    
    # Check authorization
    if post.author_id != current_user.id:
        flash('You do not have permission to edit this post', 'error')
        return redirect(url_for('forum.post', slug=slug))
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        body = request.form.get('body', '').strip()
        
        # Get new files
        files = []
        if 'files' in request.files:
            file_list = request.files.getlist('files')
            files = [f for f in file_list if f.filename]
        
        # Get links
        links = []
        links_input = request.form.get('links', '').strip()
        if links_input:
            links = [link.strip() for link in links_input.replace(',', '\n').split('\n') if link.strip()]
        
        updated_post, error = update_post(
            post_id=post.id,
            title=title,
            body=body,
            author_id=current_user.id,
            files=files if files else None,
            links=links if links else None
        )
        
        if updated_post:
            flash('Post updated successfully!', 'success')
            return redirect(url_for('forum.post', slug=updated_post.slug))
        else:
            flash(error or 'Error updating post', 'error')
    
    # Get existing links as string
    existing_links = '\n'.join([link.url for link in post.links])
    
    return render_template('forum/forum_edit.html',
                         post=post,
                         existing_links=existing_links)


@forum_bp.route('/<slug>/delete', methods=['POST'])
@login_required
def delete(slug):
    """Delete a forum post"""
    post = ForumPost.query.filter_by(slug=slug).first_or_404()
    
    is_admin = current_user.is_admin or current_user.role == 'admin'
    success, error = delete_post(post.id, current_user.id, is_admin=is_admin)
    
    if success:
        flash('Post deleted successfully', 'success')
        return redirect(url_for('forum.index'))
    else:
        flash(error or 'Error deleting post', 'error')
        return redirect(url_for('forum.post', slug=slug))


@forum_bp.route('/<slug>/comment', methods=['POST'])
@login_required
def comment(slug):
    """Add a comment to a post"""
    # Check if user is banned
    if is_user_banned(current_user.id):
        return jsonify({'success': False, 'message': 'You are banned from posting in the forum'}), 403
    
    post = ForumPost.query.filter_by(slug=slug).first_or_404()
    
    body = request.form.get('body', '').strip()
    file = request.files.get('file') if 'file' in request.files else None
    
    if file and not file.filename:
        file = None
    
    comment_obj, error = create_comment(
        post_id=post.id,
        body=body,
        author_id=current_user.id,
        file=file
    )
    
    if comment_obj:
        return jsonify({
            'success': True,
            'message': 'Comment added successfully',
            'comment_id': comment_obj.id
        })
    else:
        return jsonify({
            'success': False,
            'message': error or 'Error adding comment'
        }), 400


@forum_bp.route('/<slug>/react', methods=['POST'])
@login_required
def react(slug):
    """React to a post (like/dislike)"""
    post = ForumPost.query.filter_by(slug=slug).first_or_404()
    
    if not request.is_json:
        return jsonify({'success': False, 'message': 'Content-Type must be application/json'}), 400
    
    reaction_type = request.json.get('reaction_type', 'like')
    
    success, error, data = toggle_reaction(
        user_id=current_user.id,
        post_id=post.id,
        reaction_type=reaction_type
    )
    
    if success:
        return jsonify({
            'success': True,
            **data
        })
    else:
        return jsonify({
            'success': False,
            'message': error or 'Error reacting to post'
        }), 400


@forum_bp.route('/comment/<int:comment_id>/react', methods=['POST'])
@login_required
def react_comment(comment_id):
    """React to a comment (like/dislike)"""
    comment_obj = ForumComment.query.get_or_404(comment_id)
    
    reaction_type = request.json.get('reaction_type', 'like')
    
    success, error, data = toggle_reaction(
        user_id=current_user.id,
        comment_id=comment_id,
        reaction_type=reaction_type
    )
    
    if success:
        return jsonify({
            'success': True,
            **data
        })
    else:
        return jsonify({
            'success': False,
            'message': error or 'Error reacting to comment'
        }), 400


@forum_bp.route('/comment/<int:comment_id>/delete', methods=['POST'])
@login_required
def delete_comment_route(comment_id):
    """Delete a comment"""
    comment_obj = ForumComment.query.get_or_404(comment_id)
    post = comment_obj.post
    
    is_admin = current_user.is_admin or current_user.role == 'admin'
    success, error = delete_comment(comment_id, current_user.id, is_admin=is_admin)
    
    if success:
        flash('Comment deleted successfully', 'success')
    else:
        flash(error or 'Error deleting comment', 'error')
    
    # Redirect back to post
    return redirect(url_for('forum.post', slug=post.slug))


# ============ ADMIN ROUTES ============

@forum_bp.route('/admin')
@login_required
@admin_required
def admin_manager():
    """Admin forum management page"""
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()
    filter_type = request.args.get('filter', 'all')  # all, locked, featured, highlighted
    
    # Posts query
    posts_query = ForumPost.query
    
    if search:
        posts_query = posts_query.filter(
            or_(
                ForumPost.title.ilike(f'%{search}%'),
                ForumPost.body.ilike(f'%{search}%')
            )
        )
    
    if filter_type == 'locked':
        posts_query = posts_query.filter_by(is_locked=True)
    elif filter_type == 'featured':
        posts_query = posts_query.filter_by(is_featured=True)
    elif filter_type == 'highlighted':
        posts_query = posts_query.filter_by(is_highlighted=True)
    
    posts = posts_query.order_by(desc(ForumPost.created_at)).paginate(page=page, per_page=20, error_out=False)
    
    # Get all comments
    comments_query = ForumComment.query
    if search:
        comments_query = comments_query.filter(ForumComment.body.ilike(f'%{search}%'))
    comments = comments_query.order_by(desc(ForumComment.created_at)).limit(50).all()
    
    # Get banned users
    banned_users = ForumBan.query.filter_by(is_active=True).all()
    
    return render_template('admin/admin/forum_manager.html',
                         posts=posts,
                         comments=comments,
                         banned_users=banned_users,
                         search=search,
                         filter_type=filter_type)


@forum_bp.route('/admin/post/<int:post_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_post(post_id):
    """Admin delete post"""
    success, error = delete_post(post_id, current_user.id, is_admin=True)
    
    if success:
        flash('Post deleted successfully', 'success')
    else:
        flash(error or 'Error deleting post', 'error')
    
    return redirect(url_for('forum.admin_manager',
                          page=request.args.get('page', 1),
                          search=request.args.get('search', ''),
                          filter=request.args.get('filter', 'all')))


@forum_bp.route('/admin/comment/<int:comment_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_comment(comment_id):
    """Admin delete comment"""
    success, error = delete_comment(comment_id, current_user.id, is_admin=True)
    
    if success:
        flash('Comment deleted successfully', 'success')
    else:
        flash(error or 'Error deleting comment', 'error')
    
    return redirect(url_for('forum.admin_manager',
                          page=request.args.get('page', 1),
                          search=request.args.get('search', ''),
                          filter=request.args.get('filter', 'all')))


@forum_bp.route('/admin/user/<int:user_id>/ban', methods=['POST'])
@login_required
@admin_required
def admin_ban_user(user_id):
    """Admin ban user from forum"""
    reason = request.form.get('reason', '').strip()
    success, error = ban_user(user_id, current_user.id, reason=reason if reason else None)
    
    if success:
        flash('User banned successfully', 'success')
    else:
        flash(error or 'Error banning user', 'error')
    
    return redirect(url_for('forum.admin_manager',
                          page=request.args.get('page', 1),
                          search=request.args.get('search', ''),
                          filter=request.args.get('filter', 'all')))


@forum_bp.route('/admin/user/<int:user_id>/unban', methods=['POST'])
@login_required
@admin_required
def admin_unban_user(user_id):
    """Admin unban user from forum"""
    success, error = unban_user(user_id)
    
    if success:
        flash('User unbanned successfully', 'success')
    else:
        flash(error or 'Error unbanning user', 'error')
    
    return redirect(url_for('forum.admin_manager',
                          page=request.args.get('page', 1),
                          search=request.args.get('search', ''),
                          filter=request.args.get('filter', 'all')))


@forum_bp.route('/admin/post/<int:post_id>/lock', methods=['POST'])
@login_required
@admin_required
def admin_lock_post(post_id):
    """Admin lock post (disable comments)"""
    post = ForumPost.query.get_or_404(post_id)
    post.is_locked = True
    db.session.commit()
    flash('Post locked successfully', 'success')
    return redirect(url_for('forum.admin_manager',
                          page=request.args.get('page', 1),
                          search=request.args.get('search', ''),
                          filter=request.args.get('filter', 'all')))


@forum_bp.route('/admin/post/<int:post_id>/unlock', methods=['POST'])
@login_required
@admin_required
def admin_unlock_post(post_id):
    """Admin unlock post (enable comments)"""
    post = ForumPost.query.get_or_404(post_id)
    post.is_locked = False
    db.session.commit()
    flash('Post unlocked successfully', 'success')
    return redirect(url_for('forum.admin_manager',
                          page=request.args.get('page', 1),
                          search=request.args.get('search', ''),
                          filter=request.args.get('filter', 'all')))


@forum_bp.route('/admin/post/<int:post_id>/feature', methods=['POST'])
@login_required
@admin_required
def admin_feature_post(post_id):
    """Admin feature post"""
    post = ForumPost.query.get_or_404(post_id)
    post.is_featured = True
    db.session.commit()
    flash('Post featured successfully', 'success')
    return redirect(url_for('forum.admin_manager',
                          page=request.args.get('page', 1),
                          search=request.args.get('search', ''),
                          filter=request.args.get('filter', 'all')))


@forum_bp.route('/admin/post/<int:post_id>/unfeature', methods=['POST'])
@login_required
@admin_required
def admin_unfeature_post(post_id):
    """Admin unfeature post"""
    post = ForumPost.query.get_or_404(post_id)
    post.is_featured = False
    db.session.commit()
    flash('Post unfeatured successfully', 'success')
    return redirect(url_for('forum.admin_manager',
                          page=request.args.get('page', 1),
                          search=request.args.get('search', ''),
                          filter=request.args.get('filter', 'all')))


@forum_bp.route('/admin/post/<int:post_id>/highlight', methods=['POST'])
@login_required
@admin_required
def admin_highlight_post(post_id):
    """Admin highlight post"""
    post = ForumPost.query.get_or_404(post_id)
    post.is_highlighted = True
    db.session.commit()
    flash('Post highlighted successfully', 'success')
    return redirect(url_for('forum.admin_manager',
                          page=request.args.get('page', 1),
                          search=request.args.get('search', ''),
                          filter=request.args.get('filter', 'all')))


@forum_bp.route('/admin/post/<int:post_id>/unhighlight', methods=['POST'])
@login_required
@admin_required
def admin_unhighlight_post(post_id):
    """Admin unhighlight post"""
    post = ForumPost.query.get_or_404(post_id)
    post.is_highlighted = False
    db.session.commit()
    flash('Post unhighlighted successfully', 'success')
    return redirect(url_for('forum.admin_manager',
                          page=request.args.get('page', 1),
                          search=request.args.get('search', ''),
                          filter=request.args.get('filter', 'all')))


@forum_bp.route('/admin/file/<int:file_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_file(file_id):
    """Admin delete file from post"""
    success, error = delete_file_from_post(file_id, current_user.id, is_admin=True)
    
    if success:
        flash('File deleted successfully', 'success')
    else:
        flash(error or 'Error deleting file', 'error')
    
    return redirect(url_for('forum.admin_manager',
                          page=request.args.get('page', 1),
                          search=request.args.get('search', ''),
                          filter=request.args.get('filter', 'all')))

