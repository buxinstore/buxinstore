#!/usr/bin/env python3
"""
Script to fix product image paths in the database.

This script normalizes all product image paths to the format:
'uploads/products/{filename}'

It handles various existing path formats:
- 'products/{filename}' -> 'uploads/products/{filename}'
- '/static/uploads/products/{filename}' -> 'uploads/products/{filename}'
- 'static/uploads/products/{filename}' -> 'uploads/products/{filename}'
- 'uploads/products/{filename}' -> (no change, already correct)
- Any other format will be normalized
"""

import os
import sys
from pathlib import Path

# Add the parent directory to the path so we can import the app
sys.path.insert(0, str(Path(__file__).parent))

from app import create_app, db
from app import Product

def normalize_image_path(path):
    """
    Normalize image path to 'uploads/products/{filename}' format.
    
    Args:
        path: The image path from database
        
    Returns:
        Normalized path or None if path is invalid
    """
    if not path:
        return None
    
    # Remove leading/trailing whitespace
    path = path.strip()
    
    # Handle None or empty strings
    if not path:
        return None
    
    # Remove leading slash if present
    if path.startswith('/'):
        path = path[1:]
    
    # Handle different path formats
    if path.startswith('static/uploads/products/'):
        # Remove 'static/' prefix
        path = path.replace('static/', '', 1)
    elif path.startswith('uploads/products/'):
        # Already in correct format
        pass
    elif path.startswith('products/'):
        # Add 'uploads/' prefix
        path = f'uploads/{path}'
    elif path.startswith('/static/uploads/products/'):
        # Remove leading slash and 'static/' prefix
        path = path.replace('/static/', '', 1)
    else:
        # If it's just a filename, assume it's in products folder
        if '/' not in path:
            path = f'uploads/products/{path}'
        # Otherwise, try to extract filename and rebuild path
        else:
            filename = os.path.basename(path)
            path = f'uploads/products/{filename}'
    
    return path

def fix_product_image_paths():
    """Fix all product image paths in the database."""
    app = create_app()
    
    with app.app_context():
        # Get all products
        products = Product.query.all()
        
        print(f"Found {len(products)} products in database")
        print("-" * 60)
        
        fixed_count = 0
        skipped_count = 0
        error_count = 0
        
        for product in products:
            if not product.image:
                skipped_count += 1
                continue
            
            original_path = product.image
            normalized_path = normalize_image_path(original_path)
            
            if normalized_path != original_path:
                try:
                    # Verify the file exists
                    static_folder = app.static_folder
                    full_path = os.path.join(static_folder, normalized_path)
                    
                    if os.path.exists(full_path):
                        product.image = normalized_path
                        fixed_count += 1
                        print(f"✓ Fixed: {product.name}")
                        print(f"  Old: {original_path}")
                        print(f"  New: {normalized_path}")
                    else:
                        # Try to find the file with original path
                        old_full_path = os.path.join(static_folder, original_path.lstrip('/'))
                        if os.path.exists(old_full_path):
                            # File exists with old path, update DB anyway
                            product.image = normalized_path
                            fixed_count += 1
                            print(f"✓ Fixed (path updated, file may need moving): {product.name}")
                            print(f"  Old: {original_path}")
                            print(f"  New: {normalized_path}")
                        else:
                            error_count += 1
                            print(f"✗ Error: File not found for {product.name}")
                            print(f"  Path: {original_path}")
                            print(f"  Normalized: {normalized_path}")
                            print(f"  Checked: {full_path}")
                except Exception as e:
                    error_count += 1
                    print(f"✗ Error processing {product.name}: {str(e)}")
            else:
                skipped_count += 1
        
        # Commit all changes
        if fixed_count > 0:
            try:
                db.session.commit()
                print("-" * 60)
                print(f"✓ Successfully fixed {fixed_count} product image paths")
                print(f"  Skipped: {skipped_count} (already correct or no image)")
                if error_count > 0:
                    print(f"  Errors: {error_count}")
            except Exception as e:
                db.session.rollback()
                print(f"✗ Error committing changes: {str(e)}")
                return False
        else:
            print("-" * 60)
            print("No paths needed fixing. All paths are already in correct format.")
        
        return True

if __name__ == '__main__':
    print("=" * 60)
    print("Product Image Path Fix Script")
    print("=" * 60)
    print()
    
    success = fix_product_image_paths()
    
    print()
    print("=" * 60)
    if success:
        print("Script completed successfully!")
        print()
        print("Next steps:")
        print("1. Restart your Flask application")
        print("2. Verify images are displaying correctly on product pages")
    else:
        print("Script completed with errors. Please review the output above.")
    print("=" * 60)
    
    sys.exit(0 if success else 1)

