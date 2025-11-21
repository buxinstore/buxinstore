# Discussion Forum System

A complete discussion forum system has been added to your Flask application with full admin controls.

## Features

### User Features
- ✅ Create posts with text, images, PDFs, and other files (max 5 files, 10MB each)
- ✅ Add external links (YouTube, GitHub, blogs, etc.)
- ✅ Comment on posts with optional file attachments
- ✅ Like/dislike posts and comments
- ✅ View user profiles with avatars
- ✅ Search and filter posts
- ✅ Responsive design matching your existing theme

### Admin Features
- ✅ Single powerful admin control page (`/forum/admin`)
- ✅ View all posts and comments
- ✅ Search and filter posts
- ✅ Delete any post or comment
- ✅ Ban/unban users from posting
- ✅ Lock/unlock posts (disable comments)
- ✅ Feature/unfeature posts
- ✅ Highlight important posts
- ✅ View post attachments
- ✅ Manage banned users list

## Installation

1. **Run the migration:**
   ```bash
   flask db upgrade
   ```

2. **The system is ready to use!**

## Routes

### User Routes
- `/forum` - Forum index (list all posts)
- `/forum/create` - Create new post (login required)
- `/forum/<slug>` - View single post
- `/forum/<slug>/edit` - Edit post (author only)
- `/forum/<slug>/delete` - Delete post (author or admin)
- `/forum/<slug>/comment` - Add comment (POST, login required)
- `/forum/<slug>/react` - React to post (POST, login required)
- `/forum/comment/<id>/react` - React to comment (POST, login required)
- `/forum/comment/<id>/delete` - Delete comment (author or admin)

### Admin Routes
- `/forum/admin` - Main admin control page
- `/forum/admin/post/<id>/delete` - Delete post
- `/forum/admin/comment/<id>/delete` - Delete comment
- `/forum/admin/user/<id>/ban` - Ban user
- `/forum/admin/user/<id>/unban` - Unban user
- `/forum/admin/post/<id>/lock` - Lock post
- `/forum/admin/post/<id>/unlock` - Unlock post
- `/forum/admin/post/<id>/feature` - Feature post
- `/forum/admin/post/<id>/unfeature` - Unfeature post
- `/forum/admin/post/<id>/highlight` - Highlight post
- `/forum/admin/post/<id>/unhighlight` - Unhighlight post
- `/forum/admin/file/<id>/delete` - Delete file from post

## File Structure

```
app/
├── models/
│   └── forum.py              # Forum models (ForumPost, ForumFile, ForumLink, ForumComment, ForumReaction, ForumBan)
├── routes/
│   └── forum.py              # Forum routes blueprint
├── services/
│   └── forum_service.py      # Business logic (file uploads, validation, reactions, etc.)
├── templates/
│   ├── forum/
│   │   ├── forum_index.html  # Forum listing page
│   │   ├── forum_post.html   # Single post view
│   │   ├── forum_create.html # Create post form
│   │   ├── forum_edit.html   # Edit post form
│   │   └── components/
│   │       ├── post_card.html    # Post card component
│   │       ├── comment.html      # Comment component
│   │       └── file_preview.html # File preview component
│   └── admin/admin/
│       └── forum_manager.html    # Admin control page
└── static/js/
    ├── forum.js              # Main forum JavaScript
    └── forum_reactions.js    # Reactions JavaScript

migrations/versions/
└── 441e9e9c2468_add_forum_models.py  # Database migration
```

## Database Models

### ForumPost
- Title, body, slug
- Author (user_id)
- Timestamps (created_at, updated_at)
- Admin flags (is_locked, is_featured, is_highlighted)
- Relationships: files, links, comments, reactions

### ForumFile
- File URL (Cloudinary)
- Public ID for deletion
- Filename, type, size
- Belongs to ForumPost

### ForumLink
- URL, title, link_type
- Belongs to ForumPost

### ForumComment
- Body, optional file attachment
- Author, post
- Timestamps
- Reactions

### ForumReaction
- User, post or comment
- Reaction type (like/dislike)
- Unique constraint per user per post/comment

### ForumBan
- User, banned_by
- Reason, timestamps
- is_active flag

## Cloudinary Integration

The forum uses your existing Cloudinary setup:
- Images, PDFs, documents uploaded to Cloudinary
- Files stored in `forum/posts` and `forum/comments` folders
- Automatic cleanup when posts/comments are deleted
- File validation (max 10MB, no videos)

## Security

- ✅ Login required for posting/commenting
- ✅ Author-only editing/deletion (or admin)
- ✅ Admin-only routes protected
- ✅ CSRF protection on all forms
- ✅ File type and size validation
- ✅ User ban system
- ✅ Post locking (disable comments)

## Navigation

The forum is integrated into your navigation:
- **Main site**: "Discussion Forum" link in user profile menu
- **Admin panel**: "Forum Manager" link in admin sidebar

## Testing

Run the test script:
```bash
python scripts/test_forum.py
```

This will check:
- Model imports
- Route registration
- Service functions
- Database schema

## Usage Examples

### Creating a Post
1. Navigate to `/forum`
2. Click "New Post"
3. Enter title and content
4. Optionally upload files (max 5, 10MB each)
5. Optionally add external links
6. Submit

### Admin Management
1. Log in as admin
2. Go to Admin Panel → Forum Manager
3. Use tabs to view Posts, Comments, or Banned Users
4. Use search/filter to find specific content
5. Use action buttons to manage posts/users

## Notes

- Videos are not allowed (as per requirements)
- Maximum 5 files per post
- Maximum 10MB per file
- Slug is auto-generated from title
- Reactions are unique per user (one like OR dislike)
- Banned users cannot post or comment
- Locked posts cannot receive new comments

## Future Enhancements (Optional)

- Post categories/tags
- User reputation system
- Email notifications
- Rich text editor
- Post editing history
- Moderation queue
- Spam detection

