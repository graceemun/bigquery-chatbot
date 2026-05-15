# NuView BigQuery Web Application

**Live Application**: [https://bigquery-chatbot-production.up.railway.app/home)

A secure, web-based interface for NuView Analytics BigQuery data exploration powered by Claude AI and the NuView MCP BigQuery server. This application provides an intuitive chat interface for data analysis with Google OAuth authentication and persistent conversation management.

## Overview

The NuView BigQuery Web Application serves as the frontend interface to NuView's data warehouse, enabling users to:

- **Interactive Data Analysis**: Natural language queries powered by Claude Sonnet 4
- **Secure Authentication**: Google OAuth integration for user management
- **Persistent Conversations**: Save and manage multiple analysis sessions
- **Real-time BigQuery Integration**: Direct connection to NuView's data warehouse via MCP server
- **Memory-Optimized Performance**: Smart context management for extended analysis sessions

## Features

### Core Functionality

- **Claude-Powered Analysis**: Advanced AI assistant with specialized BigQuery knowledge
- **Multi-Conversation Support**: Create, rename, delete, and switch between analysis sessions
- **Cost Protection**: Built-in query cost limits and optimization suggestions
- **Smart Context Management**: Intelligent conversation compression preserving navigation capabilities
- **Real-time Response Streaming**: Live updates during analysis execution
- **Session Export**: Download conversation history as JSON

### Security & Authentication

- **Google OAuth 2.0**: Secure user authentication with account selection
- **User Session Management**: Secure session handling with automatic cleanup
- **Rate Limiting**: Per-user request throttling to prevent abuse
- **Database Security**: Encrypted user data and conversation storage

### Performance Features

- **Memory Management**: Automatic conversation cleanup and memory optimization
- **Request Timeout Protection**: Configurable timeouts for long-running queries
- **Concurrent User Support**: Thread-safe request handling for multiple users
- **Health Monitoring**: System status endpoints for monitoring and debugging

## Technology Stack

### Frontend
- **Flask**: Python web framework
- **HTML/CSS/JavaScript**: Responsive web interface
- **Google OAuth**: Authentication system
- **Railway Deployment**: Cloud hosting platform

### Backend Integration
- **Claude Sonnet 4**: AI analysis engine via Anthropic API
- **NuView MCP Server**: BigQuery connection layer
- **PostgreSQL**: User data and conversation persistence
- **Google BigQuery**: Data warehouse access

### Infrastructure
- **Railway Platform**: Production hosting
- **PostgreSQL Database**: User authentication and conversation storage
- **Environment Management**: Secure credential storage
- **SSL/TLS**: Encrypted connections

## Getting Started

### For Users

1. **Access the Application**
   - Visit: [https://nuview-bigquery.up.railway.app/](https://nuview-bigquery.up.railway.app/)
   - Click "Login with Google"
   - Select your NuView Analytics Google account

2. **Start Analysis**
   - Type natural language questions about your data
   - Example: "Show me all available datasets"
   - Example: "Analyze customer trends in the sales data"

3. **Manage Conversations**
   - Create new conversations with the "New Chat" button
   - Rename conversations by clicking the title
   - Switch between conversations using the sidebar
   - Export conversation history using "Export" option

### For Administrators

#### Local Development Setup

1. **Environment Configuration**
   ```bash
   # Authentication
   GOOGLE_CLIENT_ID=your_google_oauth_client_id
   GOOGLE_CLIENT_SECRET=your_google_oauth_secret
   GOOGLE_REDIRECT_URI=your_google_oauth_redirect_uri
   SECRET_KEY=your_flask_secret_key
   
   # Claude AI
   ANTHROPIC_API_KEY=your_anthropic_api_key
   MCP_AUDIENCE=mcp-production
   
   # Google Service Account (for MCP server authentication)
   GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account"...}
   
   # Database
   DATABASE_URL=postgresql://user:password@host:port/database
   
   # System Limits
   MAX_MEMORY_MB=400
   PORT=5000
   ```

2. **Dependencies Installation**
   ```bash
   pip install -r requirements.txt
   ```

3. **Database Setup**
   ```bash
   # Database tables are automatically created on first run
   python app.py
   ```

4. **Google OAuth Setup**
   - Create project in Google Cloud Console
   - Enable Google+ API
   - Create OAuth 2.0 credentials
   - Add authorized redirect URI: `https://your-domain.com/callback`

#### Railway Deployment

The application is configured for Railway deployment with:

- **Automatic Environment Detection**: Reads Railway environment variables
- **Database Integration**: Uses Railway's PostgreSQL addon
- **Port Configuration**: Uses Railway's dynamic port assignment
- **SSL Support**: Automatic HTTPS handling

## Application Architecture

### Request Flow

1. **User Authentication**: Google OAuth verification and session creation
2. **Message Processing**: User input validation and rate limiting
3. **Claude Integration**: Natural language processing via Anthropic API
4. **MCP Server Communication**: BigQuery operations through NuView MCP server
5. **Response Processing**: Smart formatting and context management
6. **Database Persistence**: Conversation and user data storage

### Database Schema

The PostgreSQL database includes:

- **Users Table**: Google OAuth user information and login tracking
- **Conversations Table**: Chat sessions with metadata
- **Messages Table**: Individual messages with role and content
- **Sessions Table**: Active session management

### Memory Management

- **Smart Context Compression**: Preserves navigation capabilities while reducing memory usage
- **Automatic Cleanup**: Removes old conversations when memory limits approached
- **Per-User Isolation**: Thread-safe conversation management
- **Garbage Collection**: Periodic cleanup of unused resources

## API Endpoints

### Authentication Endpoints
- `GET /` - Main page (login or redirect to home)
- `GET /login` - Initiate Google OAuth flow
- `GET /callback` - OAuth callback handler
- `GET /logout` - Clear session and logout

### Application Endpoints
- `GET /home` - Main chat interface (authenticated users only)
- `GET /get` - Process user messages and return AI responses
- `POST /clear_session` - Clear current conversation
- `GET /conversation_stats` - Get session statistics
- `GET /export_conversation` - Download conversation history

### Conversation Management
- `GET /conversations` - List user's conversations
- `POST /new_chat` - Create new conversation
- `POST /switch_conversation/<id>` - Load existing conversation
- `DELETE /delete_conversation/<id>` - Delete conversation
- `PUT /rename_conversation/<id>` - Rename conversation title

### System Endpoints
- `GET /health` - System health check
- `GET /memory-stats` - Memory usage statistics
- `GET /debug-oauth` - OAuth configuration debugging

## Configuration

### System Limits
- **Maximum Memory**: 400MB (configurable)
- **Rate Limiting**: 1 second minimum between requests per user
- **Request Timeout**: 300 seconds for BigQuery operations
- **Max Conversations**: 10 per user (with automatic cleanup)
- **Max Messages**: Intelligent compression after 40 messages

### Security Settings
- **Session Management**: Secure Flask sessions with auto-expiry
- **OAuth Scopes**: `userinfo.profile`, `userinfo.email`, `openid`
- **Database Encryption**: All user data encrypted at rest
- **Request Validation**: Input sanitization and parameter validation

## Error Handling

The application includes comprehensive error handling for:

- **Authentication Failures**: OAuth errors with helpful messages
- **BigQuery Timeout**: Automatic retry and query optimization suggestions
- **Memory Limits**: Graceful degradation with cleanup recommendations
- **Rate Limiting**: User-friendly throttling messages
- **Database Errors**: Automatic fallback and recovery procedures

## Monitoring & Logging

### Application Logging
- **Request Tracking**: All user requests logged with timing
- **Error Reporting**: Detailed error logs with stack traces
- **Performance Monitoring**: Memory usage and response time tracking
- **User Activity**: Login/logout events and session management

### Health Monitoring
- **System Status**: Memory usage, active users, request counts
- **Database Health**: Connection status and query performance
- **Claude Integration**: API response times and error rates
- **MCP Server Status**: BigQuery connection health

## Troubleshooting

### Common Issues

**Authentication Problems**
- Clear browser cookies and try again
- Verify Google account has NuView access
- Check OAuth configuration in Google Cloud Console

**Query Timeouts**
- Try smaller, more specific queries
- Use filters to reduce data processing
- Check BigQuery query cost and add limits

**Memory Issues**
- Clear conversation using "Clear Session" button
- Start new conversation for fresh memory allocation
- Contact administrator if issues persist

**Connection Errors**
- Check network connectivity
- Verify MCP server status
- Try refreshing the page

### Support

For technical issues:
1. Check the health endpoint: `/health`
2. Review browser console for JavaScript errors
3. Export conversation data before clearing session
4. Contact system administrators with specific error messages

## Security Considerations

- **No Direct Database Access**: All queries go through Claude and MCP server
- **User Isolation**: Conversations are private and user-specific
- **Audit Trail**: All user actions are logged for security review
- **Regular Security Updates**: Dependencies updated regularly
- **Environment Separation**: Development and production environments isolated

## Future Enhancements

- **Advanced Visualizations**: Charts and graphs for data analysis
- **Collaboration Features**: Share conversations with team members
- **Custom Dashboards**: Save and reuse common analysis patterns
- **API Access**: Direct API for programmatic access
- **Enhanced Export**: Multiple export formats (PDF, CSV, etc.)

## License

This application is proprietary to NuView Analytics and is intended for internal use by authorized personnel only.
