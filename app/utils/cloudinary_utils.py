"""
Cloudinary utility functions for handling file uploads
"""
import os
import cloudinary
import cloudinary.uploader
import cloudinary.api
from flask import current_app
from werkzeug.utils import secure_filename
import uuid
from datetime import datetime


def init_cloudinary(app):
    """Initialize Cloudinary with credentials from environment variables"""
    cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME')
    api_key = os.environ.get('CLOUDINARY_API_KEY')
    api_secret = os.environ.get('CLOUDINARY_API_SECRET')
    
    if not cloud_name or not api_key or not api_secret:
        app.logger.warning("⚠️ Cloudinary credentials not found in environment variables. Cloudinary uploads will fail.")
        app.logger.warning("Please set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET in your .env file")
        return False
    
    try:
        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True
        )
        app.logger.info(f"✅ Cloudinary initialized successfully (cloud_name: {cloud_name})")
        return True
    except Exception as e:
        app.logger.error(f"❌ Failed to initialize Cloudinary: {str(e)}")
        return False


def get_allowed_extensions():
    """Get allowed file extensions for uploads"""
    return {
        'jpg', 'jpeg', 'png', 'gif', 'webp',  # Images
        'mp4', 'mov', 'avi',  # Videos
        'pdf', 'docx'  # Documents
    }


def get_resource_type(filename):
    """Determine Cloudinary resource type based on file extension"""
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext in {'jpg', 'jpeg', 'png', 'gif', 'webp'}:
        return 'image'
    elif ext in {'mp4', 'mov', 'avi'}:
        return 'video'
    elif ext in {'pdf', 'docx'}:
        return 'raw'
    return 'auto'


def upload_to_cloudinary(file, folder='uploads', public_id=None, resource_type='auto'):
    """
    Upload a file to Cloudinary
    
    Args:
        file: FileStorage object from Flask request
        folder: Cloudinary folder path
        public_id: Optional public ID for the file
        resource_type: Type of resource (image, video, raw, auto)
    
    Returns:
        dict with 'url' (secure_url) and 'public_id' on success, None on failure
    """
    try:
        # Check if Cloudinary is configured
        if not cloudinary.config().cloud_name:
            error_msg = "Cloudinary is not configured. Please check your environment variables."
            current_app.logger.error(f"❌ {error_msg}")
            raise ValueError(error_msg)
        
        if not file or not hasattr(file, 'filename') or not file.filename:
            error_msg = "No file provided for upload"
            current_app.logger.error(f"❌ {error_msg}")
            raise ValueError(error_msg)
        
        # Generate unique public_id if not provided
        if not public_id:
            filename = secure_filename(file.filename)
            base, ext = os.path.splitext(filename)
            public_id = f"{folder}/{base}_{uuid.uuid4().hex[:8]}_{int(datetime.utcnow().timestamp())}"
        
        # Determine resource type if auto
        if resource_type == 'auto':
            resource_type = get_resource_type(file.filename)
        
        # Reset file pointer to beginning and read file content
        # Flask's FileStorage might have been read by form validation, so we need to reset
        try:
            # Try to seek to beginning first
            if hasattr(file, 'seek'):
                file.seek(0)
        except (AttributeError, IOError, OSError) as e:
            current_app.logger.warning(f"Could not seek file: {e}")
        
        # Read file content into memory for upload
        try:
            file_content = file.read()
            # If file was already read, try to get it from the stream
            if not file_content and hasattr(file, 'stream'):
                try:
                    if hasattr(file.stream, 'seek'):
                        file.stream.seek(0)
                    file_content = file.stream.read()
                except:
                    pass
        except Exception as e:
            error_msg = f"Error reading file: {str(e)}"
            current_app.logger.error(f"❌ {error_msg}")
            raise ValueError(error_msg)
        
        if not file_content:
            error_msg = "File is empty or could not be read. File may have been consumed by form validation."
            current_app.logger.error(f"❌ {error_msg}")
            raise ValueError(error_msg)
        
        current_app.logger.info(f"File read successfully: {len(file_content)} bytes, filename: {file.filename}")
        
        # Upload to Cloudinary using BytesIO
        from io import BytesIO
        file_stream = BytesIO(file_content)
        
        upload_result = cloudinary.uploader.upload(
            file_stream,
            folder=folder,
            public_id=public_id,
            resource_type=resource_type,
            overwrite=False,
            use_filename=False
        )
        
        # Log the full response for debugging
        current_app.logger.info(f"Cloudinary upload response: {upload_result}")
        
        secure_url = upload_result.get('secure_url')
        # Fallback to regular URL if secure_url is not available
        if not secure_url:
            secure_url = upload_result.get('url')
        
        public_id_result = upload_result.get('public_id')
        
        if not secure_url:
            error_msg = f"Upload succeeded but no URL returned from Cloudinary. Response: {upload_result}"
            current_app.logger.error(f"❌ {error_msg}")
            raise ValueError(error_msg)
        
        current_app.logger.info(f"✅ Successfully uploaded {file.filename} to Cloudinary: {secure_url}")
        
        return {
            'url': secure_url,
            'public_id': public_id_result,
            'resource_type': upload_result.get('resource_type'),
            'format': upload_result.get('format'),
            'bytes': upload_result.get('bytes')
        }
    
    except Exception as e:
        error_msg = str(e)
        current_app.logger.error(f"❌ Error uploading {file.filename if file and hasattr(file, 'filename') else 'file'} to Cloudinary: {error_msg}")
        import traceback
        current_app.logger.error(traceback.format_exc())
        return None


def upload_file_from_path(file_path, folder='uploads', public_id=None, resource_type='auto'):
    """
    Upload a file from local path to Cloudinary
    
    Args:
        file_path: Local file path
        folder: Cloudinary folder path
        public_id: Optional public ID for the file
        resource_type: Type of resource (image, video, raw, auto)
    
    Returns:
        dict with 'url' (secure_url) and 'public_id' on success, None on failure
    """
    try:
        if not os.path.exists(file_path):
            current_app.logger.error(f"File not found: {file_path}")
            return None
        
        # Generate unique public_id if not provided
        if not public_id:
            filename = os.path.basename(file_path)
            base, ext = os.path.splitext(filename)
            public_id = f"{folder}/{base}_{uuid.uuid4().hex[:8]}_{int(datetime.utcnow().timestamp())}"
        
        # Determine resource type if auto
        if resource_type == 'auto':
            resource_type = get_resource_type(file_path)
        
        # Upload to Cloudinary
        upload_result = cloudinary.uploader.upload(
            file_path,
            folder=folder,
            public_id=public_id,
            resource_type=resource_type,
            overwrite=False,
            use_filename=False
        )
        
        secure_url = upload_result.get('secure_url')
        public_id_result = upload_result.get('public_id')
        
        current_app.logger.info(f"✅ Successfully uploaded {file_path} to Cloudinary: {secure_url}")
        
        return {
            'url': secure_url,
            'public_id': public_id_result,
            'resource_type': upload_result.get('resource_type'),
            'format': upload_result.get('format'),
            'bytes': upload_result.get('bytes')
        }
    
    except Exception as e:
        current_app.logger.error(f"❌ Error uploading {file_path} to Cloudinary: {str(e)}")
        return None


def delete_from_cloudinary(public_id, resource_type='image'):
    """
    Delete a file from Cloudinary
    
    Args:
        public_id: Cloudinary public ID
        resource_type: Type of resource (image, video, raw)
    
    Returns:
        True on success, False on failure
    """
    try:
        result = cloudinary.uploader.destroy(public_id, resource_type=resource_type)
        if result.get('result') == 'ok':
            current_app.logger.info(f"✅ Successfully deleted {public_id} from Cloudinary")
            return True
        else:
            current_app.logger.warning(f"⚠️ Failed to delete {public_id} from Cloudinary: {result.get('result')}")
            return False
    except Exception as e:
        current_app.logger.error(f"❌ Error deleting {public_id} from Cloudinary: {str(e)}")
        return False


def is_cloudinary_url(url):
    """Check if a URL is a Cloudinary URL"""
    if not url:
        return False
    return 'res.cloudinary.com' in str(url) or url.startswith('http://res.cloudinary.com') or url.startswith('https://res.cloudinary.com')


def get_public_id_from_url(url):
    """Extract public_id from Cloudinary URL"""
    if not is_cloudinary_url(url):
        return None
    try:
        # Cloudinary URLs format: https://res.cloudinary.com/{cloud_name}/{resource_type}/upload/{version}/{public_id}.{format}
        parts = url.split('/upload/')
        if len(parts) > 1:
            public_id_with_version = parts[1]
            # Remove version if present
            if public_id_with_version[0].isdigit():
                public_id_with_version = '/'.join(public_id_with_version.split('/')[1:])
            # Remove format extension
            public_id = '.'.join(public_id_with_version.split('.')[:-1])
            return public_id
    except Exception as e:
        current_app.logger.error(f"Error extracting public_id from URL {url}: {str(e)}")
    return None

