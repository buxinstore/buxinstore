"""
Script to generate PWA icons from Cloudinary image URL
Requires: pip install Pillow requests
"""
import requests
from PIL import Image
import io
import os

# Cloudinary image URL
IMAGE_URL = "https://res.cloudinary.com/dfjffnmzf/image/upload/v1763661285/Tech_Buxin_is_your_gateway_to_the_future_of_technology._Whether_you_re_a_student_teacher_or_tech_enthusiast_our_app_helps_you_explore_the_exciting_world_of_robotics_coding_and_artificial_inte_dkohh9.png"

# Icon sizes to generate
ICON_SIZES = [72, 96, 128, 192, 256, 512]

# Output directory
OUTPUT_DIR = "app/static/icons"

def generate_icons():
    """Download image and generate PWA icons"""
    print(f"Downloading image from: {IMAGE_URL}")
    
    try:
        # Download the image
        response = requests.get(IMAGE_URL, timeout=30)
        response.raise_for_status()
        
        # Open image with PIL
        img = Image.open(io.BytesIO(response.content))
        
        # Convert to RGB if necessary (for PNG with transparency)
        if img.mode in ('RGBA', 'LA', 'P'):
            # Create a white background
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Ensure output directory exists
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # Generate icons for each size
        for size in ICON_SIZES:
            # Resize image maintaining aspect ratio, then crop to square
            img_copy = img.copy()
            
            # Calculate dimensions to maintain aspect ratio and fill square
            width, height = img_copy.size
            if width > height:
                # Landscape: crop width
                left = (width - height) // 2
                img_copy = img_copy.crop((left, 0, left + height, height))
            elif height > width:
                # Portrait: crop height
                top = (height - width) // 2
                img_copy = img_copy.crop((0, top, width, top + width))
            
            # Resize to target size
            img_resized = img_copy.resize((size, size), Image.Resampling.LANCZOS)
            
            # Save icon
            output_path = os.path.join(OUTPUT_DIR, f"icon-{size}.png")
            img_resized.save(output_path, "PNG", optimize=True)
            print(f"[OK] Generated: {output_path} ({size}x{size})")
        
        print(f"\n[SUCCESS] Successfully generated {len(ICON_SIZES)} icon files in {OUTPUT_DIR}/")
        return True
        
    except Exception as e:
        print(f"[ERROR] Error generating icons: {str(e)}")
        return False

if __name__ == "__main__":
    generate_icons()

