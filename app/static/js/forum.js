/**
 * Forum JavaScript - Main forum functionality
 */

// Initialize forum page
document.addEventListener('DOMContentLoaded', function() {
    // Initialize Lucide icons if available
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }
    
    // File upload preview
    const fileInputs = document.querySelectorAll('input[type="file"][name="files"]');
    fileInputs.forEach(input => {
        input.addEventListener('change', function(e) {
            const fileList = document.getElementById('file-list');
            if (!fileList) return;
            
            fileList.innerHTML = '';
            const files = Array.from(e.target.files);
            const maxFiles = 5;
            
            if (files.length > maxFiles) {
                alert(`Maximum ${maxFiles} files allowed. Only the first ${maxFiles} will be uploaded.`);
                e.target.files = Array.from(files.slice(0, maxFiles));
            }
            
            files.slice(0, maxFiles).forEach((file) => {
                const fileSize = (file.size / 1024 / 1024).toFixed(2);
                const div = document.createElement('div');
                div.className = 'flex items-center justify-between p-2 bg-[color:var(--app-input)] rounded-lg mb-2';
                div.innerHTML = `
                    <span class="text-sm text-[color:var(--app-text)]">${file.name} (${fileSize} MB)</span>
                    ${fileSize > 10 ? '<span class="text-red-500 text-xs">File too large!</span>' : ''}
                `;
                fileList.appendChild(div);
            });
        });
    });
});

// Comment form submission
function submitComment(formId, postSlug) {
    const form = document.getElementById(formId);
    if (!form) return;
    
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        
        const formData = new FormData();
        const bodyInput = form.querySelector('textarea[name="body"]');
        const fileInput = form.querySelector('input[type="file"][name="file"]');
        
        if (!bodyInput || !bodyInput.value.trim()) {
            alert('Please enter a comment');
            return;
        }
        
        formData.append('body', bodyInput.value);
        if (fileInput && fileInput.files[0]) {
            formData.append('file', fileInput.files[0]);
        }
        formData.append('csrf_token', document.querySelector('meta[name="csrf-token"]')?.content || '');
        
        const submitButton = form.querySelector('button[type="submit"]');
        const originalText = submitButton.textContent;
        submitButton.disabled = true;
        submitButton.textContent = 'Posting...';
        
        fetch(`/forum/${postSlug}/comment`, {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            } else {
                alert(data.message || 'Error posting comment');
                submitButton.disabled = false;
                submitButton.textContent = originalText;
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error posting comment');
            submitButton.disabled = false;
            submitButton.textContent = originalText;
        });
    });
}

// Export functions for use in templates
window.forumJS = {
    submitComment: submitComment
};

