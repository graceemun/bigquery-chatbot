from flask import Flask, render_template, request, jsonify, session, abort, redirect, Response, stream_template
import claude
import os
import logging
import time
import threading
import uuid
import json
import sys
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from google.auth.transport import requests 
from database import db, initialize_database
import signal
import atexit
from collections import defaultdict

app = Flask(__name__)

# Environment variables
client_id = os.environ.get('GOOGLE_CLIENT_ID')
client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')
redirect_uri = os.environ.get('GOOGLE_REDIRECT_URI')

# Configure Flask session management (no Redis needed)
app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError("SECRET_KEY environment variable is required but not set")
app.config['SESSION_PERMANENT'] = False

# Google auth config
client_config = {
    "web": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [redirect_uri]
    }
}
SCOPES = ['https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile']

flow = Flow.from_client_config(
    client_config=client_config,
    scopes=["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", "openid"],
    redirect_uri=redirect_uri
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Thread-safe request tracking - ESSENTIAL FOR CONCURRENT USERS
request_counter = 0
request_lock = threading.RLock()
user_request_counts = defaultdict(int)
user_request_lock = threading.RLock()
user_last_request = {}

# Basic rate limiting settings
MIN_REQUEST_INTERVAL = 1.0  # 1 second between requests per user
MAX_MEMORY_MB = int(os.environ.get('MAX_MEMORY_MB', '400'))

def cleanup_on_exit():
    """Clean up resources on exit"""
    logger.info("🚧¹ Cleaning up on exit...")
    # Clear conversation memory
    if hasattr(claude, 'user_conversations'):
        claude.user_conversations.clear()
    # Close database connections if they exist
    if hasattr(db, 'connection_pool') and db.connection_pool:
        try:
            db.connection_pool.closeall()
            logger.info(" Database connections closed")
        except:
            pass

# Register cleanup handlers
atexit.register(cleanup_on_exit)

def signal_handler(signum, frame):
    logger.info(f"ðŸ›‘ Received signal {signum}, cleaning up...")
    cleanup_on_exit()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)  

def get_current_user():
    """Get current authenticated Google user info"""
    if "google_id" not in session:
        return None
    
    return {
        'google_id': session['google_id'],
        'name': session['name'], 
        'email': session['email'],
        'display_name': session['name'].split()[0] if session.get('name') else 'User'  # First name only
    }

def check_rate_limit(user_id):
    """Basic rate limiting per user - ESSENTIAL FOR CONCURRENT USERS"""
    with user_request_lock:
        now = time.time()
        last_request = user_last_request.get(user_id, 0)
        
        if now - last_request < MIN_REQUEST_INTERVAL:
            return False, f"Rate limited. Please wait {MIN_REQUEST_INTERVAL - (now - last_request):.1f} seconds"
        
        user_last_request[user_id] = now
        user_request_counts[user_id] += 1
        return True, None

def require_auth():
    """Check if user is authenticated, return user info or None"""
    user = get_current_user()
    if not user:
        return None
    return user

def login_is_required(function):
    from functools import wraps
    
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "google_id" not in session:
            return abort(401)
        else:
            return function(*args, **kwargs)
    return wrapper

# Set up database when website starts up
def setup_application():
    """Initialize the application and database"""
    logger.info("Starting application setup...")
    
    # Test database connection first
    if not db.test_connection():
        logger.error("Database connection failed during startup!")
        # You can choose to continue or exit here
        # For development, continue; for production, you might want to exit
    
    # Initialize database tables
    db_success = initialize_database()
    if not db_success:
        logger.error("Database initialization failed!")
    
    logger.info("Application setup completed")
    return app

@app.route("/login")
def login():
    """Initiate Google OAuth login with forced account selection"""
    try:
        authorization_url, state = flow.authorization_url(
            prompt='select_account'  # Forces Google to show account picker
        )
        session["state"] = state
        logger.info(f"Redirecting to Google OAuth, state: {state[:8]}...")
        return redirect(authorization_url)
    except Exception as e:
        logger.error(f"❌ Login error: {str(e)}")
        return f"Login error: {str(e)}", 500

@app.route("/callback")
def callback():
    try:
        logger.info(f"Callback received: {request.args}")
        logger.info(f"Session state: {session.get('state')}")
        logger.info(f"Original Request URL: {request.url}")
        
        # Fix: Force HTTPS for OAuth (Render proxy issue)
        auth_response_url = request.url.replace('http://', 'https://')
        logger.info(f"Fixed Request URL: {auth_response_url}")
        
        flow.fetch_token(authorization_response=auth_response_url)
        logger.info("Token fetched successfully")

        if not session["state"] == request.args["state"]:
            logger.error("State mismatch")
            abort(500)

        credentials = flow.credentials
        logger.info("Credentials obtained")
        
        token_request = requests.Request()

        id_info = id_token.verify_oauth2_token(
            id_token=credentials._id_token,
            request=token_request,
            audience=client_id
        )
        logger.info("ID token verified")

        # add user information to database
        db.track_user_login(
            id_info.get("sub"), id_info.get("email"), id_info.get("name")
        )
        
        session["google_id"] = id_info.get("sub")
        session["name"] = id_info.get("name")
        session["email"] = id_info.get("email")
        
        logger.info(f"User logged in: {id_info.get('email')}")
        return redirect("/home")
        
    except Exception as e:
        import traceback
        logger.error(f"Callback error: {str(e)}")
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return f"Authentication error: {str(e)}", 500

@app.route("/")
def index():
    """Main page - shows login if not authenticated, redirects to home if authenticated"""
    if "google_id" in session:
        # User is logged in, redirect to chat interface
        return redirect("/home")
    else:
        # User not logged in, show login page
        return render_template("login.html")  # Your login page template

@app.route("/home")
@login_is_required 
def home():    
    user = get_current_user()
    user_name = user["name"]
    first_letter = user_name[0].upper() if user_name else "U"
    # Pass user info to template for display
    return render_template("index.html", first_letter=first_letter)
    
@app.route("/debug-oauth")
def debug_oauth():
    return {
        "client_id": client_id[:20] + "..." if client_id else "MISSING",
        "client_secret_set": bool(client_secret),
        "redirect_uri": "https://nuview-bigquery.up.railway.app/callback",
        "session_keys": list(session.keys())
    }

@app.route("/get")
def get_bot_response():    
    """UPDATED: Now saves messages to database conversations"""
    global request_counter
    
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    
    user_id = user['google_id']
    start_time = time.time()
    
    # Rate limiting (your existing code)
    rate_ok, rate_error = check_rate_limit(user_id)
    if not rate_ok:
        return jsonify({"error": rate_error}), 429
    
    # Memory check (your existing code)
    if hasattr(claude, 'get_memory_stats'):
        try:
            memory_stats = claude.get_memory_stats()
            memory_mb = memory_stats.get('memory_usage_mb', 0)
            
            logger.info(f"💾 Memory: {memory_mb}MB")
            
            if memory_mb > MAX_MEMORY_MB:
                return jsonify({
                    "error": f"System memory limit ({MAX_MEMORY_MB}MB) reached. Please clear your conversation and try again.",
                    "memory_stats": memory_stats,
                    "suggestion": "Use 'Clear Session' button to free up memory"
                }), 503
                
        except Exception as e:
            logger.error(f"Memory check failed: {e}")
    
    # Thread-safe request counter (your existing code)
    with request_lock:
        request_counter += 1
        current_request_id = request_counter
    
    try:
        userText = request.args.get('msg')
        conversation_id = request.args.get('conversation_id')  # NEW: Get conversation ID
        
        if not userText:
            logger.error(f"❌ Request #{current_request_id}: No message provided")
            return jsonify({"error": "No message provided"}), 400
        
        logger.info(f"📊 Request #{current_request_id} from {user['display_name']} ({user_id[:8]}...): {userText[:50]}...")
        
        # NEW: If no conversation_id provided, create a new conversation
        if not conversation_id:
            conversation_id = db.create_conversation(user_id, "New Chat")
            logger.info(f"📊 Auto-created conversation {conversation_id} for message")
        
        # NEW: Save user message to database
        if conversation_id:
            db.save_message(conversation_id, 'user', userText)
        
        # Track message in database (your existing code)
        if hasattr(db, 'track_message'):
            try:
                db.track_message(user_id, userText[:50])
            except Exception as e:
                logger.warning(f"Database tracking failed: {e}")
        
        logger.info(f"ðŸš€ Starting Claude API call at {time.time()}")
        
        # Your existing Claude API call
        response = claude.get_response_with_retry(userText, user_id, max_retries=1)
        
        logger.info(f"✅ Claude API call completed at {time.time()}")
        
        # NEW: Save assistant response to database
        if conversation_id:
            db.save_message(conversation_id, 'assistant', response)
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Your existing logging
        if duration > 30:
            logger.warning(f"ðŸ¢ Slow query completed in {duration:.2f}s: {userText[:50]}...")
        
        logger.info(f"✅ Request #{current_request_id} completed in {duration:.2f} seconds")
        
        return response
        
    except Exception as e:
        end_time = time.time()
        duration = end_time - start_time
        
        logger.error(f"❌ Request #{current_request_id} failed after {duration:.2f} seconds: {e}")
        
        if "timeout" in str(e).lower() or duration > 100:
            return jsonify({
                "error": f"Query timed out after {duration:.1f}s. BigQuery operations can take time. Try a more specific query or smaller data range.",
                "suggestion": "Try: 'Show me 3 sample tables' instead of broad queries",
                "duration": f"{duration:.2f}s"
            }), 504
        
        if duration > 25:
            logger.error(f"â±ï¸ Worker timeout detected after {duration:.2f}s")
            return jsonify({
                "error": f"Query timed out after {duration:.1f}s. This usually means the BigQuery operation is taking too long.",
                "suggestion": "Try a simpler query like 'What datasets are available?' or contact support if this persists.",
                "duration": f"{duration:.2f}s"
            }), 504
        
        return jsonify({
            "error": f"Request failed: {str(e)}",
            "request_id": current_request_id,
            "duration": f"{duration:.2f}s"
        }), 500

@app.route("/clear_session")
@login_is_required
def clear_session():
    """UPDATED: Clear the current user's conversation session"""
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    
    user_id = user['google_id']
    
    logger.info(f"🚧¹ Clear session requested by {user['display_name']} ({user_id[:8]}...)")
    
    try:
        # CRITICAL: Clear user's conversation in claude.py with user_id
        success = claude.clear_conversation(user_id)
        
        if success:
            logger.info(f"✅ Successfully cleared conversation for user {user_id[:8]}...")
            return jsonify({
                "message": f"Your conversation history has been cleared, {user['display_name']}.",
                "success": True,
                "user_name": user['display_name']
            })
        else:
            logger.info(f"â„¹ï¸ No conversation to clear for user {user_id[:8]}...")
            return jsonify({
                "message": f"No conversation found to clear, {user['display_name']}.",
                "success": False,
                "user_name": user['display_name']
            })
    except Exception as e:
        logger.error(f"❌ Error clearing conversation: {e}")
        return jsonify({
            "error": f"Failed to clear conversation: {str(e)}",
            "success": False
        }), 500

@app.route("/conversation_stats")
@login_is_required
def conversation_stats():
    """UPDATED: Get current conversation statistics"""
    user = get_current_user()
    user_id = user['google_id']

    if not user:
        return jsonify({"error": "Authentication required"}), 401
    
    try:
        # CRITICAL: Get stats for specific user
        stats = claude.get_session_stats(user_id)
        
        # Add basic user request stats
        with user_request_lock:
            user_requests = user_request_counts.get(user_id, 0)
            last_request = user_last_request.get(user_id, 0)
        
        return jsonify({
            "conversation_stats": stats,
            "request_stats": {
                "total_requests": user_requests,
                "last_request_ago": time.time() - last_request if last_request else None
            },
            "requested_by": user['display_name']
        })
    except Exception as e:
        logger.error(f"❌ Error getting conversation stats: {e}")
        return jsonify({
            "error": f"Failed to get stats: {str(e)}"
        }), 500

@app.route("/export_conversation")
@login_is_required
def export_conversation():
    """UPDATED: Export the current conversation history"""
    user = get_current_user()
    user_id = user['google_id']

    if not user:
        return jsonify({"error": "Authentication required"}), 401
    
    try:
        # CRITICAL: Export conversation for specific user
        exported_data = claude.export_conversation(user_id)
        
        logger.info(f"📊¦ Conversation exported by {user['display_name']}")
        
        response = app.response_class(
            response=exported_data,
            status=200,
            mimetype='application/json',
            headers={
                'Content-Disposition': f'attachment; filename=conversation_export_{int(time.time())}.json'
            }
        )
        return response
        
    except Exception as e:
        logger.error(f"❌ Error exporting conversation: {e}")
        return jsonify({
            "error": f"Failed to export conversation: {str(e)}"
        }), 500


@app.route("/logout")
def logout():
    """UPDATED: Clear session and redirect to login"""
    user = get_current_user()
    if user:
        logger.info(f"ðŸ‘‹ User {user['display_name']} logging out")
        
        # Optional: Clear user's conversation on logout
        try:
            claude.clear_conversation(user['google_id'])
        except Exception as e:
            logger.warning(f"Conversation cleanup on logout failed: {e}")
    
    session.clear()  # Clears all session data
    return redirect("/")  # Redirect to main page (which will show login)

@app.route("/health")
def health_check():
    """Basic health check to monitor system status"""
    try:
        # Get basic session stats
        session_stats = claude.get_session_stats() if hasattr(claude, 'get_session_stats') else {}
        user = get_current_user()
        
        # Basic memory check
        memory_info = {}
        if hasattr(claude, 'get_memory_stats'):
            try:
                memory_info = claude.get_memory_stats()
            except:
                memory_info = {"note": "Memory monitoring not available"}
        
        # Test Claude system
        claude_health = "healthy"
        try:
            if hasattr(claude, 'user_conversations'):
                active_users = len(claude.user_conversations)
            else:
                active_users = 0
        except:
            claude_health = "error"
            active_users = 0
        
        return jsonify({
            "status": "healthy",
            "timestamp": time.time(),
            "total_requests_processed": request_counter,
            "claude_system": {
                "status": claude_health,
                "active_users": active_users
            },
            "memory": memory_info,
            "current_user": user['display_name'] if user else "anonymous"
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500
    
# Add memory monitoring route - basic version
@app.route("/memory-stats")
@login_is_required
def memory_stats():
    """Basic memory usage monitoring"""
    try:
        user = get_current_user()
        
        if hasattr(claude, 'get_memory_stats'):
            memory_stats = claude.get_memory_stats()
        else:
            memory_stats = {"note": "Memory monitoring not available"}
        
        return jsonify({
            "memory_stats": memory_stats,
            "timestamp": time.time(),
            "user": user['display_name'],
            "total_requests": request_counter
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/conversations', methods=['GET'])
@login_is_required
def get_conversations():
    """Get all conversations for the current user"""
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    
    try:
        conversations = db.get_user_conversations(user['google_id'])
        return jsonify({
            'success': True,
            'conversations': conversations,
            'user': user['display_name']
        })
    except Exception as e:
        logger.error(f"❌ Error getting conversations: {e}")
        return jsonify({
            'success': False,
            'error': f"Failed to load conversations: {str(e)}"
        }), 500

@app.route('/new_chat', methods=['POST'])
@login_is_required
def new_chat():
    """Create a new conversation"""
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    
    try:
        # Create new conversation in database
        conversation_id = db.create_conversation(user['google_id'], "New Chat")
        
        if not conversation_id:
            return jsonify({
                'success': False,
                'error': 'Failed to create conversation'
            }), 500
        
        # Clear the in-memory conversation for this user
        claude.clear_conversation(user['google_id'])
        
        logger.info(f"📊 New chat created: {conversation_id} for {user['display_name']}")
        
        return jsonify({
            'success': True,
            'conversation_id': conversation_id,
            'message': f'New conversation started for {user["display_name"]}',
            'user': user['display_name']
        })
        
    except Exception as e:
        logger.error(f"❌ Error creating new chat: {e}")
        return jsonify({
            'success': False,
            'error': f"Failed to create new chat: {str(e)}"
        }), 500

@app.route('/switch_conversation/<conversation_id>', methods=['POST'])
@login_is_required
def switch_conversation(conversation_id):
    """Switch to an existing conversation"""
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    
    try:
        # Security check: verify user owns this conversation
        owner = db.get_conversation_owner(conversation_id)
        if owner != user['google_id']:
            return jsonify({
                'success': False,
                'error': 'Conversation not found or access denied'
            }), 404
        
        # Get conversation messages from database
        messages = db.get_conversation_messages(conversation_id)
        
        # Clear current in-memory conversation
        claude.clear_conversation(user['google_id'])
        
        logger.info(f"ðŸ”„ User {user['display_name']} switched to conversation {conversation_id}")
        
        return jsonify({
            'success': True,
            'conversation_id': conversation_id,
            'messages': messages,
            'user': user['display_name']
        })
        
    except Exception as e:
        logger.error(f"❌ Error switching conversation: {e}")
        return jsonify({
            'success': False,
            'error': f"Failed to switch conversation: {str(e)}"
        }), 500

@app.route('/delete_conversation/<conversation_id>', methods=['DELETE'])
@login_is_required
def delete_conversation(conversation_id):
    """Delete a conversation"""
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    
    try:
        # Delete conversation (includes security check)
        success = db.delete_conversation(conversation_id, user['google_id'])
        
        if not success:
            return jsonify({
                'success': False,
                'error': 'Conversation not found or access denied'
            }), 404
        
        logger.info(f"🚧¹ Conversation {conversation_id} deleted by {user['display_name']}")
        
        return jsonify({
            'success': True,
            'message': 'Conversation deleted successfully',
            'user': user['display_name']
        })
        
    except Exception as e:
        logger.error(f"❌ Error deleting conversation: {e}")
        return jsonify({
            'success': False,
            'error': f"Failed to delete conversation: {str(e)}"
        }), 500

@app.route('/rename_conversation/<conversation_id>', methods=['PUT'])
@login_is_required
def rename_conversation(conversation_id):
    """Rename a conversation title with enhanced debugging"""
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    
    try:
        logger.info(f"🏷️ Rename request for conversation {conversation_id} by {user['display_name']}")
        
        # Get the new title from request data
        data = request.get_json()
        logger.info(f"📊‹ Request data: {data}")
        
        if not data or 'title' not in data:
            logger.error("❌ No title provided in request")
            return jsonify({
                'success': False,
                'error': 'Title is required'
            }), 400
        
        new_title = data['title'].strip()
        logger.info(f"📊 New title: '{new_title}'")
        
        # Validate title length
        if not new_title:
            logger.error("❌ Empty title provided")
            return jsonify({
                'success': False,
                'error': 'Title cannot be empty'
            }), 400
        
        if len(new_title) > 100:
            logger.error(f"❌ Title too long: {len(new_title)} characters")
            return jsonify({
                'success': False,
                'error': 'Title must be 100 characters or less'
            }), 400
        
        # Security check: verify user owns this conversation
        logger.info(f"ðŸ”’ Checking ownership for conversation {conversation_id}")
        owner = db.get_conversation_owner(conversation_id)
        logger.info(f"🚧‘â€ðŸ’» Conversation owner: {owner}, Current user: {user['google_id']}")
        
        if owner != user['google_id']:
            logger.error(f"❌ Access denied: {user['google_id']} tried to rename conversation owned by {owner}")
            return jsonify({
                'success': False,
                'error': 'Conversation not found or access denied'
            }), 404
        
        # Update the title in database
        logger.info(f"💾 Calling db.rename_conversation...")
        success = db.rename_conversation(conversation_id, new_title)
        logger.info(f"💾 Database result: {success}")
        
        if not success:
            logger.error("❌ Database rename operation failed")
            return jsonify({
                'success': False,
                'error': 'Failed to rename conversation in database'
            }), 500
        
        logger.info(f"✅ Conversation {conversation_id} renamed to '{new_title}' by {user['display_name']}")
        
        return jsonify({
            'success': True,
            'message': 'Conversation renamed successfully',
            'new_title': new_title,
            'user': user['display_name']
        })
        
    except Exception as e:
        logger.error(f"❌ Error in rename_conversation route: {str(e)}")
        logger.error(f"❌ Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"❌ Full traceback: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': f"Failed to rename conversation: {str(e)}"
        }), 500


if __name__ == '__main__':
    PORT = int(os.environ.get("PORT", 5000))
    
    logger.info("Starting Flask with concurrent user support (minimal)")
    
    # Call setup_application instead of setup_database
    app = setup_application()
    
    app.run(
        host='0.0.0.0', 
        port=PORT,
        threaded=True,
        debug=False
    )
