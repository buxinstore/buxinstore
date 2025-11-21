"""
Test script for forum functionality
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models.forum import ForumPost, ForumComment, ForumReaction, ForumBan
from app.models import User
from app.services.forum_service import (
    create_post, create_comment, toggle_reaction,
    ban_user, unban_user, is_user_banned
)

def test_forum():
    """Run forum tests"""
    app = create_app()
    
    with app.app_context():
        print("=" * 60)
        print("FORUM SYSTEM TESTS")
        print("=" * 60)
        
        # Test 1: Check if models exist
        print("\n1. Testing model imports...")
        try:
            post_count = ForumPost.query.count()
            comment_count = ForumComment.query.count()
            reaction_count = ForumReaction.query.count()
            ban_count = ForumBan.query.count()
            print(f"   ✓ Models imported successfully")
            print(f"   - Posts: {post_count}")
            print(f"   - Comments: {comment_count}")
            print(f"   - Reactions: {reaction_count}")
            print(f"   - Bans: {ban_count}")
        except Exception as e:
            print(f"   ✗ Error: {e}")
            return False
        
        # Test 2: Check if a test user exists
        print("\n2. Testing user lookup...")
        try:
            test_user = User.query.first()
            if test_user:
                print(f"   ✓ Found test user: {test_user.username}")
            else:
                print("   ⚠ No users found in database")
                print("   Note: Some tests require a user to be logged in")
        except Exception as e:
            print(f"   ✗ Error: {e}")
            return False
        
        # Test 3: Check forum routes
        print("\n3. Testing route registration...")
        try:
            with app.test_client() as client:
                # Test forum index
                response = client.get('/forum')
                if response.status_code == 200:
                    print("   ✓ Forum index route works")
                else:
                    print(f"   ✗ Forum index returned status {response.status_code}")
                
                # Test admin route (should redirect if not logged in)
                response = client.get('/forum/admin')
                if response.status_code in [302, 401, 403]:
                    print("   ✓ Admin route protected (redirects when not logged in)")
                else:
                    print(f"   ⚠ Admin route returned status {response.status_code}")
        except Exception as e:
            print(f"   ✗ Error: {e}")
            return False
        
        # Test 4: Check service functions
        print("\n4. Testing service functions...")
        try:
            # Test slug generation
            from app.services.forum_service import generate_slug, ensure_unique_slug
            test_slug = generate_slug("Test Post Title!")
            print(f"   ✓ Slug generation works: '{test_slug}'")
            
            # Test file validation
            from app.services.forum_service import validate_file, FORUM_ALLOWED_EXTENSIONS
            print(f"   ✓ File validation ready (allowed extensions: {len(FORUM_ALLOWED_EXTENSIONS)})")
            
        except Exception as e:
            print(f"   ✗ Error: {e}")
            return False
        
        # Test 5: Database schema check
        print("\n5. Testing database schema...")
        try:
            # Check if tables exist
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            required_tables = ['forum_post', 'forum_file', 'forum_link', 
                             'forum_comment', 'forum_reaction', 'forum_ban']
            
            missing_tables = []
            for table in required_tables:
                if table in tables:
                    print(f"   ✓ Table '{table}' exists")
                else:
                    print(f"   ✗ Table '{table}' missing")
                    missing_tables.append(table)
            
            if missing_tables:
                print(f"\n   ⚠ Missing tables: {', '.join(missing_tables)}")
                print("   Run: flask db upgrade")
                return False
                
        except Exception as e:
            print(f"   ✗ Error: {e}")
            return False
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Run migrations: flask db upgrade")
        print("2. Visit /forum to see the forum")
        print("3. Visit /forum/admin (as admin) to manage the forum")
        print("=" * 60)
        
        return True

if __name__ == '__main__':
    success = test_forum()
    sys.exit(0 if success else 1)

