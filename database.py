import psycopg2
from psycopg2.extras import RealDictCursor
import uuid
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class SimpleDB:
    """Basic database operations for user tracking"""
    
    def __init__(self):
        self.database_url = os.environ.get('DATABASE_URL')
        if not self.database_url:
            logger.warning("DATABASE_URL not found - database features disabled")
            self.enabled = False
        else:
            self.enabled = True
            logger.info(f"Database enabled with URL: {self.database_url[:50]}...")
    
    def get_connection(self):
        """Get database connection with error handling"""
        if not self.enabled:
            logger.warning("Database not enabled, returning None connection")
            return None
        
        try:
            conn = psycopg2.connect(self.database_url, cursor_factory=RealDictCursor)
            logger.debug("Database connection established")
            return conn
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            return None
    
    def test_connection(self):
        """Test if database connection works"""
        try:
            conn = self.get_connection()
            if conn:
                with conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1")
                    result = cursor.fetchone()
                    logger.info("Database connection test successful")
                    return True
            return False
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False
    
    def create_tables_if_needed(self):
        """Create all tables - FIXED VERSION with better error handling"""
        if not self.enabled:
            logger.warning("Database not enabled, skipping table creation")
            return False
        
        logger.info("Starting table creation process...")
        
        try:
            conn = self.get_connection()
            if not conn:
                logger.error("Could not establish database connection for table creation")
                return False
                
            with conn:
                cursor = conn.cursor()
                
                logger.info("Creating users table...")
                # Create users table first (no dependencies)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        google_id VARCHAR(255) UNIQUE NOT NULL,
                        email VARCHAR(255) NOT NULL,
                        name VARCHAR(255),
                        first_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        total_messages INTEGER DEFAULT 0
                    )
                """)
                
                logger.info("Creating user_activity table...")
                # Create user_activity table with proper foreign key
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_activity (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        google_id VARCHAR(255) REFERENCES users(google_id) ON DELETE CASCADE,
                        action VARCHAR(100),
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        details TEXT
                    )
                """)
                
                logger.info("Creating conversations table...")
                # Create conversations table with proper foreign key
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        google_id VARCHAR(255) NOT NULL REFERENCES users(google_id) ON DELETE CASCADE,
                        title VARCHAR(500) DEFAULT 'New Chat',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        message_count INTEGER DEFAULT 0,
                        is_active BOOLEAN DEFAULT TRUE
                    )
                """)
                
                logger.info("Creating messages table...")
                # Create messages table with proper foreign key
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                        role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant')),
                        content TEXT NOT NULL,
                        message_order INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                logger.info("Creating indexes...")
                # Create indexes
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_conversations_user 
                    ON conversations(google_id, updated_at DESC)
                """)
                
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_messages_conversation 
                    ON messages(conversation_id, message_order ASC)
                """)
                
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_user_activity_user
                    ON user_activity(google_id, timestamp DESC)
                """)
                
                # Commit the transaction
                conn.commit()
                logger.info("All tables and indexes created successfully")
                
                # Verify tables were created
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name IN ('users', 'user_activity', 'conversations', 'messages')
                    ORDER BY table_name
                """)
                
                created_tables = cursor.fetchall()
                table_names = [table['table_name'] for table in created_tables]
                logger.info(f"Verified tables exist: {table_names}")
                
                if len(table_names) == 4:
                    logger.info("✅ All 4 tables successfully created and verified")
                    return True
                else:
                    logger.error(f"❌ Expected 4 tables, found {len(table_names)}: {table_names}")
                    return False
            
        except psycopg2.Error as e:
            logger.error(f"PostgreSQL error during table creation: {e}")
            logger.error(f"Error code: {e.pgcode}")
            logger.error(f"Error message: {e.pgerror}")
            return False
            
        except Exception as e:
            logger.error(f"Unexpected error during table creation: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
    
    def track_user_login(self, google_id: str, email: str, name: str):
        """Track when user logs in - FIXED VERSION"""
        if not self.enabled:
            logger.warning("Database not enabled, skipping user login tracking")
            return None
        
        logger.info(f"Tracking login for user: {email}")
        
        try:
            conn = self.get_connection()
            if not conn:
                logger.error("No database connection for user login tracking")
                return None
                
            with conn:
                cursor = conn.cursor()
                
                # Check if user exists
                cursor.execute(
                    "SELECT id FROM users WHERE google_id = %s",
                    (google_id,)
                )
                user = cursor.fetchone()
                
                if user:
                    # Update last login
                    cursor.execute(
                        "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE google_id = %s",
                        (google_id,)
                    )
                    logger.info(f"Updated login time for existing user: {email}")
                else:
                    # Create new user
                    cursor.execute(
                        """INSERT INTO users (google_id, email, name) 
                           VALUES (%s, %s, %s)""",
                        (google_id, email, name)
                    )
                    logger.info(f"Created new user: {email}")
                
                # Log the login activity
                cursor.execute(
                    """INSERT INTO user_activity (google_id, action, details) 
                       VALUES (%s, %s, %s)""",
                    (google_id, 'login', f"User {name} logged in")
                )
                
                conn.commit()
                logger.info(f"Successfully tracked login for: {email}")
                
        except Exception as e:
            logger.error(f"Failed to track user login for {email}: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")

    def get_user_conversations(self, google_id: str):
        """Get all conversations for a user, ordered by most recent"""
        if not self.enabled:
            logger.warning("Database not enabled, returning empty conversations list")
            return []
        
        try:
            conn = self.get_connection()
            if not conn:
                logger.error("No database connection for get_user_conversations")
                return []
                
            with conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT id, title, created_at, updated_at, message_count
                    FROM conversations 
                    WHERE google_id = %s
                    ORDER BY updated_at DESC
                    LIMIT 50
                """, (google_id,))
                
                conversations = cursor.fetchall()
                
                # Convert to list of dicts with string IDs
                result = []
                for conv in conversations:
                    result.append({
                        'id': str(conv['id']),
                        'title': conv['title'],
                        'created_at': conv['created_at'].isoformat(),
                        'updated_at': conv['updated_at'].isoformat(),
                        'message_count': conv['message_count']
                    })
                
                logger.info(f"Retrieved {len(result)} conversations for user {google_id[:8]}...")
                return result
                
        except Exception as e:
            logger.error(f"Failed to get conversations for user {google_id[:8]}...: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return []

    def save_message(self, conversation_id: str, role: str, content: str):
        """Save a message to a conversation with proper message ordering"""
        if not self.enabled:
            logger.warning("Database not enabled, cannot save message")
            return False
        
        try:
            conn = self.get_connection()
            if not conn:
                logger.error("No database connection for save_message")
                return False
                
            with conn:
                cursor = conn.cursor()
                
                # Get the next message order number for this conversation
                cursor.execute("""
                    SELECT COALESCE(MAX(message_order), 0) + 1 as next_order
                    FROM messages 
                    WHERE conversation_id = %s
                """, (conversation_id,))
                
                next_order = cursor.fetchone()['next_order']
                
                # Insert the message with proper ordering
                cursor.execute("""
                    INSERT INTO messages (conversation_id, role, content, message_order, created_at) 
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                """, (conversation_id, role, content, next_order))
                
                # Update conversation metadata
                cursor.execute("""
                    UPDATE conversations 
                    SET updated_at = CURRENT_TIMESTAMP, 
                        message_count = message_count + 1
                    WHERE id = %s
                """, (conversation_id,))
                
                # Auto-generate title from first user message if still "New Chat"
                if role == 'user':
                    cursor.execute("""
                        UPDATE conversations 
                        SET title = %s 
                        WHERE id = %s AND title = 'New Chat'
                    """, (content[:50] + "..." if len(content) > 50 else content, conversation_id))
                
                conn.commit()
                logger.info(f"Message saved: {role} message #{next_order} to conversation {conversation_id}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to save message to conversation {conversation_id}: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False

    def get_conversation_messages(self, conversation_id: str):
        """Get all messages for a conversation ordered by message_order"""
        if not self.enabled:
            logger.warning("Database not enabled, returning empty messages list")
            return []
        
        try:
            conn = self.get_connection()
            if not conn:
                logger.error("No database connection for get_conversation_messages")
                return []
                
            with conn:
                cursor = conn.cursor()
                
                # Order by message_order instead of created_at
                cursor.execute("""
                    SELECT role, content, created_at, message_order
                    FROM messages 
                    WHERE conversation_id = %s
                    ORDER BY COALESCE(message_order, 0) ASC, created_at ASC
                """, (conversation_id,))
                
                messages = cursor.fetchall()
                
                # Convert to format expected by frontend
                result = []
                for msg in messages:
                    result.append({
                        'sender': msg['role'],  # 'user' or 'assistant'
                        'text': msg['content'],
                        'timestamp': msg['created_at'].isoformat()
                    })
                
                logger.info(f"Retrieved {len(result)} messages for conversation {conversation_id}")
                return result
                
        except Exception as e:
            logger.error(f"Failed to get messages for conversation {conversation_id}: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return []

    def get_conversation_owner(self, conversation_id: str):
        """Get the owner of a conversation for security checks"""
        if not self.enabled:
            logger.warning("Database not enabled, cannot get conversation owner")
            return None
        
        try:
            conn = self.get_connection()
            if not conn:
                logger.error("No database connection for get_conversation_owner")
                return None
                
            with conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT google_id 
                    FROM conversations 
                    WHERE id = %s
                """, (conversation_id,))
                
                result = cursor.fetchone()
                owner = result['google_id'] if result else None
                logger.info(f"Conversation {conversation_id} owned by: {owner[:8] if owner else 'None'}...")
                return owner
                
        except Exception as e:
            logger.error(f"Failed to get conversation owner for {conversation_id}: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return None

    def rename_conversation(self, conversation_id: str, new_title: str):
        """Rename a conversation title"""
        if not self.enabled:
            logger.warning("Database not enabled for rename_conversation")
            return False
        
        try:
            conn = self.get_connection()
            if not conn:
                logger.error("No database connection for rename_conversation")
                return False
                
            with conn:
                cursor = conn.cursor()
                
                logger.info(f"Attempting to rename conversation {conversation_id} to '{new_title}'")
                
                # Update the conversation title
                cursor.execute("""
                    UPDATE conversations 
                    SET title = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (new_title, conversation_id))
                
                rows_affected = cursor.rowcount
                logger.info(f"Rename query affected {rows_affected} rows")
                
                if rows_affected > 0:
                    conn.commit()
                    logger.info(f"Conversation {conversation_id} renamed to '{new_title}'")
                    return True
                else:
                    logger.warning(f"No conversation found with ID: {conversation_id}")
                    return False
                
        except Exception as e:
            logger.error(f"Failed to rename conversation {conversation_id}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False

    def delete_conversation(self, conversation_id: str, google_id: str):
        """Delete a conversation permanently (hard delete)"""
        if not self.enabled:
            logger.warning("Database not enabled for delete_conversation")
            return False
        
        try:
            conn = self.get_connection()
            if not conn:
                logger.error("No database connection for delete_conversation")
                return False
                
            with conn:
                cursor = conn.cursor()
                
                # First verify ownership for security
                cursor.execute("""
                    SELECT id FROM conversations 
                    WHERE id = %s AND google_id = %s
                """, (conversation_id, google_id))
                
                if not cursor.fetchone():
                    logger.warning(f"Attempted to delete non-existent or unauthorized conversation: {conversation_id}")
                    return False
                
                # Get message count before deletion for logging
                cursor.execute("""
                    SELECT COUNT(*) as message_count 
                    FROM messages 
                    WHERE conversation_id = %s
                """, (conversation_id,))
                message_count = cursor.fetchone()['message_count']
                
                # Hard delete: Remove conversation (messages will cascade delete due to FK constraint)
                cursor.execute("""
                    DELETE FROM conversations 
                    WHERE id = %s AND google_id = %s
                """, (conversation_id, google_id))
                
                if cursor.rowcount > 0:
                    conn.commit()
                    logger.info(f"HARD DELETE: Conversation {conversation_id} and {message_count} messages permanently deleted by user {google_id[:8]}...")
                    return True
                else:
                    logger.warning(f"No conversation was deleted")
                    return False
                
        except Exception as e:
            logger.error(f"Failed to delete conversation {conversation_id}: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
    
    def track_message(self, google_id: str, message_preview: str):
        """Track when user sends a message"""
        if not self.enabled:
            return
        
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Increment user message count
                cursor.execute(
                    "UPDATE users SET total_messages = total_messages + 1 WHERE google_id = %s",
                    (google_id,)
                )
                
                # Log the activity
                cursor.execute(
                    """INSERT INTO user_activity (google_id, action, details) 
                       VALUES (%s, %s, %s)""",
                    (google_id, 'message_sent', message_preview[:100])
                )
                
                conn.commit()
                
        except Exception as e:
            logger.error(f"Failed to track message: {e}")

    # Add all your other methods here...
    # I'll include a few key ones with better error handling
    
    def create_conversation(self, google_id: str, title: str = "New Chat"):
        """Create a new conversation and return its ID"""
        if not self.enabled:
            logger.warning("Database not enabled, cannot create conversation")
            return None
        
        logger.info(f"Creating conversation '{title}' for user {google_id[:8]}...")
        
        try:
            conn = self.get_connection()
            if not conn:
                logger.error("No database connection for conversation creation")
                return None
                
            with conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO conversations (google_id, title) 
                    VALUES (%s, %s) 
                    RETURNING id
                """, (google_id, title))
                
                conversation_id = cursor.fetchone()['id']
                conn.commit()
                
                logger.info(f"New conversation created: {conversation_id} for user {google_id[:8]}...")
                return str(conversation_id)
                
        except Exception as e:
            logger.error(f"Failed to create conversation: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return None

# Create global database instance
db = SimpleDB()

# Add initialization function that can be called explicitly
def initialize_database():
    """Initialize database tables - call this during app startup"""
    logger.info("Initializing database...")
    
    if not db.test_connection():
        logger.error("Database connection test failed during initialization")
        return False
    
    success = db.create_tables_if_needed()
    if success:
        logger.info("Database initialization completed successfully")
    else:
        logger.error("Database initialization failed")
    
    return success