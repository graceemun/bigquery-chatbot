import anthropic
from anthropic import Anthropic
import os
from dotenv import load_dotenv
import random
import time
import logging
import uuid
import json
from datetime import datetime, timedelta, timezone
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import gc
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import psutil
from typing import Dict, List, Any

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Anthropic client
client = Anthropic()

# Configuration
url = os.getenv("MCP_URL")
MAX_CONVERSATIONS_PER_USER = 10
MAX_MESSAGES_PER_CONVERSATION = 20
MEMORY_CLEANUP_INTERVAL = 25

# Global variables
user_conversations = {}
request_count = 0

# Thread-safe locks
_token_lock = threading.RLock()
_conversation_locks = {}
_locks_lock = threading.RLock()
_cached = {"token": None, "expiry": None}


def get_system_prompt():
    today = datetime.now().strftime("%B %d, %Y")
    return f"""You are an expert BigQuery data analyst.
Today's date is {today}. Always use this when interpreting relative terms like "this year", "this month", "this quarter", or "recent".
Provide comprehensive analysis by intelligently breaking complex questions into steps when needed.

When users want to manage BigQuery projects, use natural language understanding:

PROJECT CONFIGURATION:
- "add/connect/configure new project" → Use configure_bigquery("add", ...)
- "list/show projects" → Use configure_bigquery("list") 
- "remove/delete project" → Use configure_bigquery("remove", ...)
- "test connection" → Use configure_bigquery("test", ...)

For adding projects, collect information conversationally:
1. Ask for project nickname (user-friendly name)
2. Ask for Google Cloud Project ID  
3. Request service account JSON (guide them to Google Cloud Console)
4. Optional description

Walk users through the process step-by-step rather than requiring all parameters upfront.

MANDATORY: 
NEVER use more than 8 tools in any single response. 
Count as you go: 
1. First tool used 
2. Second tool used   
3. Third tool used 
4. Fourth tool used 
5. Fifth tool used 
6. Sixth tool used 
7. Seventh tool used
8. Eighth tool used - STOP HERE and provide analysis  
If more analysis needed, end with: "Continue for detailed calculations"

CRITICAL: If a query fails with "Table not found", NEVER assume the table exists in a different format. 
Always use list_table_ids() first to verify actual table names before building queries.
NEVER invent or assume table names - only use confirmed existing tables.
NEVER use fabricated data
NEVER use fake results without actually querying the database

CRITICAL RULES:
- Always use list_dataset_ids() first to see available datasets
- Never use project names as dataset names in list_table_ids()
- NEVER assume column names - always check schema first using get_table_info
- Design every query to complete in <20 seconds (add LIMIT, filters)
- NEVER provide entity names or details without actually querying the database first

DATA FRESHNESS PRIORITY:
When multiple tables exist for similar data, ALWAYS prioritize current/active tables:
- Check LastModifiedTime in table metadata to identify recent vs legacy tables
- Prefer tables with recent modification dates (within 6 months)
- Look for "active_flag" or similar status columns to filter current records
- Avoid tables with names suggesting legacy data: "*_old", "*_archive", "*_historical"
- When finding older tables (>1 year), actively search for newer equivalents

BUSINESS-FIRST DISCOVERY:
When users ask about entities (clients, projects, people), think business process:
- Client questions → Look for client/customer tables in business datasets
- Project questions → Look for project/engagement tables  
- Time/hours questions → Look for time tracking/event tables
- People questions → Look for employee/user/person tables

AUTOMATIC FALLBACK SEARCH:
When any query returns zero results, immediately try alternative data sources:
1. Primary table search (master/structured data)
2. If empty → Try operational tables (time tracking, events, activities)  
3. If empty → Try related tables (different naming patterns)
4. If empty → Search across multiple relevant tables for entity

NEVER accept "no data found" without trying 2-3 different table types.

Smart Discovery Patterns:
```sql
-- RIGHT: Business-first with fallback
Tool 1: list_dataset_ids()  
Tool 2: list_table_ids("Capacity_Planning")  
Tool 3: get_table_info("Capacity_Planning", "primary_table")  
Tool 4: execute_sql("SELECT * FROM primary_table WHERE condition LIMIT 10")
-- If Tool 4 returns zero: automatically try alternative tables
Tool 5: execute_sql("SELECT * FROM alternative_table WHERE condition LIMIT 10")

-- Table Prioritization:
1. Business tables: *_client, *_project, *_employee, user
2. Time tracking: timely, *_events, *_hours, *_time
3. AVOID: Generic string_field_X, economic data, staging tables
TIMEOUT PREVENTION:

Always use LIMIT ≤ 100 for exploration
Add date filters: WHERE date >= '2024-01-01'
Use COUNT(*) to test size before complex queries
Never SELECT * without LIMIT on large tables

SCHEMA VALIDATION:

Use get_table_info() before complex multi-table queries
Verify relationships and column names
Test with small LIMIT first

MULTI-STEP TRIGGERS:

Complex business questions requiring detailed analysis
Multiple table joins and calculations
When estimating 8+ tools needed
Queries might hit timeout limits

STEP FORMAT:
"📋 Step [X] Complete: [Step Name]
Key Discoveries: [findings]
Ready for Step [X+1]: [Next Step] - say 'continue'"

ERROR PREVENTION:

Column not found: Use get_table_info first
No data found: Try alternative tables automatically
Timeout: Add LIMIT and filters
Explain data source differences when found in alternative tables

Always provide thorough, business-ready analysis with automatic fallback discovery."""

# Smart Context Management
class SmartContextManager:
    """Manages conversation context while preserving accuracy"""
    
    def __init__(self):
        self.essential_patterns = [
            'schema', 'table_structure', 'column_names', 'relationships', 
            'join_conditions', 'primary_keys', 'foreign_keys'
        ]
        
    def analyze_tool_response(self, content: str) -> Dict[str, Any]:
        """Analyze tool response to extract essential info"""
        try:
            data = json.loads(content)
            
            analysis = {
                'type': 'unknown',
                'essential_data': {},
                'can_compress': False,
                'compression_safe': True
            }
            
            if 'schema' in data:
                analysis['type'] = 'schema'
                analysis['essential_data'] = {
                    'table': data.get('table'),
                    'columns': [f"{col['name']}({col['type']})" for col in data.get('schema', [])[:10]],
                    'total_columns': data.get('total_columns'),
                    'num_rows': data.get('num_rows')
                }
                analysis['can_compress'] = len(data.get('schema', [])) > 10
                
            elif 'results' in data and isinstance(data['results'], list):
                analysis['type'] = 'query_results'
                results = data['results']
                analysis['essential_data'] = {
                    'row_count': len(results),
                    'columns': list(results[0].keys()) if results else [],
                    'sample_data': results[:2],
                    'query_summary': data.get('query', '')[:100]
                }
                analysis['can_compress'] = len(results) > 5
                
            elif 'tables' in data and isinstance(data['tables'], list):
                analysis['type'] = 'table_list'
                tables = data['tables']
                analysis['essential_data'] = {
                    'table_count': len(tables),
                    'important_tables': [t for t in tables if t.get('importance_score', 0) > 10][:5],
                    'categories': data.get('category_summary', {}),
                    'dataset': data.get('dataset_id')
                }
                analysis['can_compress'] = len(tables) > 10
                
            return analysis
            
        except Exception:
            return {'type': 'raw', 'can_compress': True, 'compression_safe': True}
    
    def create_smart_summary(self, tool_responses: List[Dict]) -> str:
        """Create intelligent summary preserving key navigation info"""
        
        summaries = []
        schemas_found = []
        tables_discovered = []
        queries_executed = []
        
        for response in tool_responses:
            analysis = response.get('analysis', {})
            essential = analysis.get('essential_data', {})
            
            if analysis.get('type') == 'schema':
                schemas_found.append(f"{essential.get('table')} ({essential.get('total_columns')} cols)")
                
            elif analysis.get('type') == 'table_list':
                tables_discovered.append(f"{essential.get('table_count')} tables in {essential.get('dataset')}")
                
            elif analysis.get('type') == 'query_results':
                queries_executed.append(f"{essential.get('row_count')} rows from query")
        
        if schemas_found:
            summaries.append(f"📋 **Schemas explored**: {', '.join(schemas_found[:3])}")
        if tables_discovered:
            summaries.append(f"📊 **Tables discovered**: {', '.join(tables_discovered)}")
        if queries_executed:
            summaries.append(f"🔍 **Queries run**: {len(queries_executed)} successful")
            
        return "\n".join(summaries)
    
    def compress_response_intelligently(self, content: str, analysis: Dict) -> str:
        """Compress while preserving navigation-critical info"""
        
        if not analysis.get('can_compress', False):
            return content
            
        try:
            data = json.loads(content)
            essential = analysis['essential_data']
            
            if analysis['type'] == 'schema':
                compressed = {
                    "type": "schema_summary",
                    "table": essential['table'],
                    "columns": essential['columns'],
                    "total_columns": essential['total_columns'],
                    "num_rows": essential['num_rows'],
                    "note": "Compressed - ask for specific columns if needed"
                }
                
            elif analysis['type'] == 'query_results':
                compressed = {
                    "type": "query_summary", 
                    "row_count": essential['row_count'],
                    "columns": essential['columns'],
                    "sample_data": essential['sample_data'],
                    "query": essential['query_summary'],
                    "note": f"Showing 2 of {essential['row_count']} rows - ask for specific data if needed"
                }
                
            elif analysis['type'] == 'table_list':
                compressed = {
                    "type": "table_summary",
                    "dataset": essential['dataset'], 
                    "total_tables": essential['table_count'],
                    "important_tables": essential['important_tables'],
                    "categories": essential['categories'],
                    "note": "Compressed list - ask about specific tables for details"
                }
                
            else:
                return content
                
            return json.dumps(compressed, indent=1)
            
        except Exception:
            return content
        
# Main Response Function
class RequestTimeout:
    def __init__(self, seconds):
        self.seconds = seconds
        self.executor = ThreadPoolExecutor(max_workers=1)
    
    def __enter__(self):
        return self
    
    def __exit__(self, type, value, traceback):
        self.executor.shutdown(wait=False)
    
    def run_with_timeout(self, func, *args, **kwargs):
        future = self.executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=self.seconds)
        except FuturesTimeoutError:
            raise TimeoutError(f"Operation timed out after {self.seconds} seconds")

# Utility Functions
def ensure_aware_utc(dt):
    """Normalize datetime to UTC"""
    if dt is None:
        return None
    if isinstance(dt, str):
        s = dt.strip()
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        try:
            return datetime.fromisoformat(s).astimezone(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)
    if getattr(dt, 'tzinfo', None) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def now_utc():
    return datetime.now(timezone.utc)

# Authentication
class TimeoutRequest(Request):
    def __init__(self, timeout=10):
        super().__init__()
        self.timeout = timeout
    
    def __call__(self, *args, **kwargs):
        kwargs['timeout'] = self.timeout
        return super().__call__(*args, **kwargs)

def get_google_identity_token(target_audience: str) -> str:
    try:
        sa_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        creds = service_account.IDTokenCredentials.from_service_account_info(
            sa_info, target_audience=target_audience
        )
        timeout_request = TimeoutRequest(timeout=10)
        creds.refresh(timeout_request)
        return creds.token, creds.expiry
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        raise Exception(f"Authentication timeout: {str(e)}")

def get_cached_token(aud: str) -> str:
    with _token_lock:
        now = now_utc()
        exp = ensure_aware_utc(_cached.get("expiry"))
        if _cached.get("token") and exp and (exp - now).total_seconds() > 120:
            return _cached["token"]
        
        try:
            token, expiry = get_google_identity_token(aud)
            _cached.update({"token": token, "expiry": ensure_aware_utc(expiry)})
            return token
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            if _cached.get("token"):
                logger.warning("Using potentially expired token as fallback")
                return _cached["token"]
            raise

# Memory Management
def get_memory_stats():
    """Get current memory usage stats"""
    try:
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        return {
            "memory_usage_mb": round(memory_info.rss / 1024 / 1024, 2),
            "memory_percent": round(process.memory_percent(), 2),
            "active_conversations": len(user_conversations),
            "total_messages": sum(len(conv['history']) for conv in user_conversations.values())
        }
    except ImportError:
        return {
            "active_conversations": len(user_conversations),
            "total_messages": sum(len(conv['history']) for conv in user_conversations.values()),
            "note": "Install psutil for detailed memory stats"
        }

# Threading Helpers
def get_user_lock(user_id):
    with _locks_lock:
        if user_id not in _conversation_locks:
            _conversation_locks[user_id] = threading.RLock()
        return _conversation_locks[user_id]

def cleanup_user_lock(user_id):
    with _locks_lock:
        _conversation_locks.pop(user_id, None)

# Conversation Management
def initialize_conversation(user_id):
    session_id = str(uuid.uuid4())[:12]
    user_conversations[user_id] = {
        'history': [],
        'metadata': {
            'started_at': datetime.now(timezone.utc),
            'last_activity': datetime.now(timezone.utc),
            'message_count': 0,
            'session_id': session_id,
            'user_id': user_id
        }
    }
    logger.info(f"🆕 New conversation initialized for user {user_id}: {session_id}")
    return session_id

def get_user_conversation(user_id):
    user_lock = get_user_lock(user_id)
    with user_lock:
        if user_id not in user_conversations:
            initialize_conversation(user_id)
        return user_conversations[user_id]


def get_tool_responses_for_summary(user_id: str) -> List[Dict]:
    """Extract tool responses for summary creation"""
    if user_id not in user_conversations:
        return []
        
    context_manager = SmartContextManager()
    tool_responses = []
    
    for msg in user_conversations[user_id]['history']:
        if msg['role'] == 'assistant':
            content = msg.get('content', '')
            if any(pattern in content.lower() for pattern in ['"success":', '"results":', '"schema":']):
                analysis = context_manager.analyze_tool_response(content)
                tool_responses.append({
                    'content': content,
                    'analysis': analysis,
                    'timestamp': msg.get('timestamp')
                })
    
    return tool_responses

def smart_context_compression(user_id: str):
    """Compress context while preserving navigation capabilities"""
    if user_id not in user_conversations:
        return
        
    user_conv = user_conversations[user_id]
    history = user_conv['history']
    
    if len(history) < 6:
        return
        
    context_manager = SmartContextManager()
    tool_responses = get_tool_responses_for_summary(user_id)
    
    # Create intelligent summary
    navigation_summary = context_manager.create_smart_summary(tool_responses)
    
    # Keep last 2 exchanges + summary
    recent_exchanges = history[-4:]
    
    # Create summary message
    summary_msg = {
        "role": "assistant",
        "content": f"**Session Summary** (Context optimized for continued navigation):\n\n{navigation_summary}\n\n*All table schemas, relationships, and query patterns remain accessible. Ask about any specific table, column, or data point.*",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_navigation_summary": True
    }
    
    # Replace history
    user_conv['history'] = [summary_msg] + recent_exchanges
    
    logger.info(f"🧠 Smart compression: {len(history)} → {len(user_conv['history'])} messages, navigation preserved")

def enhanced_add_to_conversation(user_id: str, role: str, content: str):
    """Enhanced conversation management with smart compression"""
    global request_count
    request_count += 1
    
    user_lock = get_user_lock(user_id)
    with user_lock:
        user_conv = get_user_conversation(user_id)
        
        # Smart compression of incoming tool responses
        if role == "assistant" and len(content) > 2000:
            context_manager = SmartContextManager()
            analysis = context_manager.analyze_tool_response(content)
            
            if analysis.get('can_compress', False):
                compressed_content = context_manager.compress_response_intelligently(content, analysis)
                if len(compressed_content) < len(content) * 0.7:
                    content = compressed_content
                    logger.info(f"📦 Compressed {analysis['type']} response")
        
        # Add the message
        user_conv['history'].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        user_conv['metadata']['last_activity'] = datetime.now(timezone.utc)
        user_conv['metadata']['message_count'] += 1
        
        # Check for smart compression trigger
        
        message_count = len(user_conv['history'])
        
        # Trigger smart compression before hitting limits
        if message_count >= 40:  # Much higher threshold for accuracy
            smart_context_compression(user_id)
        
        # Less frequent global cleanup
        if request_count % (MEMORY_CLEANUP_INTERVAL * 4) == 0:
            cleanup_old_conversations()

def add_to_conversation(user_id: str, role: str, content: str):
    """Smart conversation management without accuracy loss"""
    return enhanced_add_to_conversation(user_id, role, content)

def get_conversation_for_api(user_id):
    """Get conversation history in Claude API format"""
    user_conv = get_user_conversation(user_id)
    api_history = []
    for msg in user_conv['history']:
        if not msg.get('is_system_message', False) and not msg.get('is_summary', False):
            api_history.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    return api_history

def clear_conversation(user_id):
    """Clear conversation and start fresh"""
    user_lock = get_user_lock(user_id)
    with user_lock:
        if user_id in user_conversations:
            old_session = user_conversations[user_id]['metadata'].get('session_id', 'none')
            del user_conversations[user_id]
            logger.info(f"🧹 Conversation cleared for user {user_id}. Previous session: {old_session}")
            cleanup_user_lock(user_id)
            return True
        else:
            logger.info(f"🧹 No conversation to clear for user {user_id}")
            return False

def cleanup_old_conversations():
    """Clean up old conversations to free memory"""
    global user_conversations
    
    with _locks_lock:
        if len(user_conversations) <= MAX_CONVERSATIONS_PER_USER:
            return
        
        sorted_users = sorted(
            user_conversations.items(), 
            key=lambda x: x[1]['metadata']['last_activity'],
            reverse=True
        )
        
        keep_users = dict(sorted_users[:MAX_CONVERSATIONS_PER_USER])
        removed_users = set(user_conversations.keys()) - set(keep_users.keys())
        removed_count = len(removed_users)
        
        user_conversations.clear()
        user_conversations.update(keep_users)
        
        for user_id in removed_users:
            _conversation_locks.pop(user_id, None)
        
        gc.collect()
        logger.info(f"🧹 Memory cleanup: removed {removed_count} old conversations")

def get_conversation_history(user_id):
    """Get full conversation history"""
    if user_id not in user_conversations:
        return {
            "history": [],
            "metadata": {"user_id": user_id, "note": "No conversation found"}
        }
    
    user_conv = user_conversations[user_id]
    return {
        "history": user_conv['history'],
        "metadata": user_conv['metadata']
    }

def export_conversation(user_id):
    """Export conversation as JSON"""
    if user_id not in user_conversations:
        return json.dumps({
            "error": "No conversation found for user",
            "user_id": user_id,
            "exported_at": datetime.now(timezone.utc).isoformat()
        }, indent=2)
    
    user_conv = user_conversations[user_id]
    export_data = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "metadata": user_conv['metadata'],
        "conversation": user_conv['history']
    }
    return json.dumps(export_data, indent=2, default=str)

def get_session_stats(user_id=None):
    """Get conversation session statistics"""
    if not user_id:
        return {
            "total_active_users": len(user_conversations),
            "active_sessions": [
                {
                    "user_id": uid,
                    "session_id": conv['metadata']['session_id'],
                    "message_count": conv['metadata']['message_count'],
                    "last_activity": conv['metadata']['last_activity'].isoformat()
                }
                for uid, conv in user_conversations.items()
            ]
        }
    
    if user_id not in user_conversations:
        return {
            "session_active": False,
            "user_id": user_id,
            "message_count": 0,
            "note": "No active conversation for this user"
        }
    
    user_conv = user_conversations[user_id]
    metadata = user_conv['metadata']
    
    session_duration = None
    if metadata['started_at']:
        start_time = ensure_aware_utc(metadata['started_at'])
        session_duration = (now_utc() - start_time).total_seconds() if start_time else None
    
    return {
        "session_active": True,
        "user_id": user_id,
        "session_id": metadata['session_id'],
        "message_count": metadata['message_count'],
        "started_at": metadata['started_at'].isoformat() if metadata['started_at'] else None,
        "last_activity": metadata['last_activity'].isoformat() if metadata['last_activity'] else None,
        "session_duration_seconds": session_duration,
        "conversation_length": len(user_conv['history'])
    }

# Response Processing
def extract_text_from_response(response):
    text_parts = []
    for block in response.content:
        if hasattr(block, 'text'):
            text_parts.append(block.text)
    
    full_text = ' '.join(text_parts)
    logger.info(f"📝 Response length: {len(full_text)} characters")
    return full_text

def get_tool(response):
    for block in response.content:
        if hasattr(block, 'name'):
            name = block.name
            logger.info(f"--TOOL USED: {name}--")
            if hasattr(block, 'is_error') and block.is_error:
                logger.error(f"❌ Tool {name} returned error")
            if hasattr(block, 'content'):
                logger.info(f"📊 Tool {name} response size: {len(str(block.content))} chars")

def remove_debug_text(response_text):
    """Remove debugging language from Claude responses"""
    if "##" in response_text:
        parts = response_text.split("##", 1)
        if len(parts) > 1:
            return "## " + parts[1].strip()
    
    content_indicators = [
        "Dataset Overview", "Customer Sample Data", "Key Observations", 
        "Project:", "Total Datasets", "Schema for", "Sample data",
        "Results:", "Data:", "Unable to retrieve", "Tables in"
    ]
    
    lines = response_text.split('\n')
    
    for i, line in enumerate(lines):
        debug_phrases = [
            "i'll retrieve", "i'll try", "i'll get", "i'll use", "i'll query",
            "let me", "now i'll", "finally i'll", "attempting to", "unable to access", "perfect!"
        ]
        
        if any(phrase in line.lower() for phrase in debug_phrases):
            continue
            
        if any(indicator in line for indicator in content_indicators):
            return '\n'.join(lines[i:]).strip()
    
    filtered_lines = []
    found_content = False
    
    for line in lines:
        line_lower = line.lower().strip()
        
        if any(phrase in line_lower for phrase in [
            "i'll retrieve", "i'll try", "i'll use", "let me", "now i'll",
            "attempting to", "unable to access", "i apologize"
        ]):
            continue
        
        if line.strip():
            found_content = True
        
        if found_content:
            filtered_lines.append(line)
    
    return '\n'.join(filtered_lines).strip() if filtered_lines else response_text.strip()

def generate_request_id():
    return str(uuid.uuid4())[:8]

def build_mcp_token(user_id: str, user_email: str) -> str:
    """Build the MCP_API_KEY:base64(context) token the MCP server expects."""
    import base64, json
    api_key = os.environ["MCP_API_KEY"]
    context = base64.b64encode(
        json.dumps({"user_id": user_id, "email": user_email}).encode()
    ).decode()
    return f"{api_key}:{context}"


def get_response(user_input, user_id, user_email=None, request_id=None):
    """Get response with proactive context management"""

    if not request_id:
        request_id = generate_request_id()

    start_time = time.time()

    def _execute_request():

        token = build_mcp_token(user_id, user_email or f"{user_id}@unknown")

        user_conv = get_user_conversation(user_id)
        session_id = user_conv['metadata'].get('session_id', 'new')
        
        logger.info(f"📡 [{request_id}] Claude API call for user {user_id}, session {session_id}")
        
        add_to_conversation(user_id, "user", user_input)
        api_conversation = get_conversation_for_api(user_id)
        
        response = client.beta.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=12000,
            timeout=300.0,
            system = [
                {
                    "type": "text",
                    "text": get_system_prompt(),
                    "cache_control": {
                        "type": "ephemeral"
                    }
                }
            ],
            messages=api_conversation,
            mcp_servers=[
                {
                    "type": "url",
                    "url": url,
                    "name": "NuView-bqMCP",
                    "authorization_token": token,
                }
            ],
            extra_headers={
                "anthropic-beta": "mcp-client-2025-04-04"
            }
        )
        
        get_tool(response)
        clean_response = extract_text_from_response(response)
        final_response = remove_debug_text(clean_response)
        add_to_conversation(user_id, "assistant", final_response)
        
        return final_response
    
    try:
        with RequestTimeout(300) as timeout_ctx:
            result = timeout_ctx.run_with_timeout(_execute_request)
            
        api_duration = time.time() - start_time
        logger.info(f"✅ [{request_id}] Request completed in {api_duration:.2f} seconds")
        return result
        
    except TimeoutError as e:
        duration = time.time() - start_time
        logger.error(f"⏱️ [{request_id}] Request timeout after {duration:.2f} seconds: {e}")
        return f"Request timed out after {duration:.1f} seconds. Please try a simpler query."
        

def get_response_with_retry(user_input, user_id, user_email=None, max_retries=2):
    """Get response with retry logic"""

    request_id = generate_request_id()
    total_start_time = time.time()

    user_conv = get_user_conversation(user_id)
    session_id = user_conv['metadata'].get('session_id', 'new')

    logger.info(f"🚀 [{request_id}] Processing request for user {user_id}, session: {session_id}")

    for attempt in range(max_retries):
        try:
            attempt_start = time.time()
            logger.info(f"📡 [{request_id}] Attempt {attempt + 1}/{max_retries}")

            result = get_response(user_input, user_id, user_email=user_email, request_id=request_id)
            
            attempt_duration = time.time() - attempt_start
            total_duration = time.time() - total_start_time
            
            logger.info(f"✅ [{request_id}] Request successful on attempt {attempt + 1}")
            logger.info(f"   Attempt time: {attempt_duration:.2f}s, Total time: {total_duration:.2f}s")
            
            return result
            
        except Exception as e:
            attempt_duration = time.time() - attempt_start
            logger.error(f"💥 [{request_id}] Error on attempt {attempt + 1} after {attempt_duration:.2f}s: {str(e)}")
            
            if attempt < max_retries - 1:
                logger.info(f"🔄 [{request_id}] Retrying in 2 seconds...")
                time.sleep(2)
            else:
                total_duration = time.time() - total_start_time
                logger.error(f"💥 [{request_id}] All retry attempts failed after {total_duration:.2f}s")
                return f"Request failed after {max_retries} attempts. Please try a simpler query or try again later."
    
    # This should never be reached, but just in case
    return "Unexpected error in retry logic. Please try again."
