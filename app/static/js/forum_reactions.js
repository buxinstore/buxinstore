/**
 * Forum Reactions JavaScript - Handle likes/dislikes
 */

// React to a post
function reactToPost(slug, reactionType, postId = null) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
    
    fetch(`/forum/${slug}/react`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({ reaction_type: reactionType })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Update counts - use postId if provided (for index page), otherwise use generic selectors (for post page)
            if (postId) {
                // Index page - update specific post counts
                const likeCountEl = document.querySelector(`.post-like-count-${postId}`);
                const dislikeCountEl = document.querySelector(`.post-dislike-count-${postId}`);
                
                if (likeCountEl) {
                    likeCountEl.textContent = data.like_count;
                }
                if (dislikeCountEl) {
                    dislikeCountEl.textContent = data.dislike_count;
                }
            } else {
                // Post page - update generic selectors
                const likeCountEl = document.querySelector('.post-like-count');
                const dislikeCountEl = document.querySelector('.post-dislike-count');
                
                if (likeCountEl) {
                    likeCountEl.textContent = data.like_count;
                }
                if (dislikeCountEl) {
                    dislikeCountEl.textContent = data.dislike_count;
                }
            }
            
            // Reload page to update button states
            window.location.reload();
        } else {
            alert(data.message || 'Error reacting to post');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Error reacting to post');
    });
}

// React to a comment
function reactToComment(commentId, reactionType) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
    
    fetch(`/forum/comment/${commentId}/react`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({ reaction_type: reactionType })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Update counts
            const likeCountEl = document.querySelector(`.comment-like-count-${commentId}`);
            const dislikeCountEl = document.querySelector(`.comment-dislike-count-${commentId}`);
            
            if (likeCountEl) {
                likeCountEl.textContent = data.like_count;
            }
            if (dislikeCountEl) {
                dislikeCountEl.textContent = data.dislike_count;
            }
            
            // Reload page to update button states
            window.location.reload();
        } else {
            alert(data.message || 'Error reacting to comment');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Error reacting to comment');
    });
}

// Export functions for global use
window.reactToPost = reactToPost;
window.reactToComment = reactToComment;

