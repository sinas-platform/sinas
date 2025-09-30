# Maestro Cloud Functions Platform

**Build intelligent multi-agent systems and automated workflows with simple Python functions.**

Maestro is a cloud functions platform that lets you create sophisticated automation systems by writing small, focused Python functions that work together. Think of it as building blocks for AI agents, workflow automation, and intelligent systems.

## What Can You Build?

### 🤖 **Multi-Agent Systems**
Create AI agents that collaborate on complex tasks:
- **Document Processing Agent**: Fetches, analyzes, and extracts data from documents
- **Validation Agent**: Checks data quality and business rules
- **Decision Agent**: Makes intelligent routing decisions based on analysis
- **Notification Agent**: Sends alerts and updates to teams
- **Orchestrator Agent**: Coordinates the entire workflow

Each agent is a function that can call other functions, creating a network of specialized AI workers.

### 🔄 **Intelligent Workflows**
Build workflows that adapt and respond:
- **Customer Support**: Automatically categorize tickets, route to specialists, and track resolution
- **Content Pipeline**: Process uploads, extract metadata, generate thumbnails, and notify teams
- **Data Processing**: Validate inputs, transform data, run analysis, and generate reports
- **Invoice Processing**: Extract data, validate customers, check compliance, and route for approval

### 🌐 **Event-Driven Automation**
React to events in real-time:
- **Webhook Endpoints**: Respond to external system events (GitHub pushes, payment notifications, form submissions)
- **Scheduled Tasks**: Run periodic maintenance, generate reports, sync data
- **Chain Reactions**: One function triggers another, creating cascading automation

### 💡 **Key Features**

**Everything is a Function**: No complex workflow engines or YAML configs. Just Python functions that call other Python functions.

**Automatic Orchestration**: Functions discover and call each other automatically. Write `validate_customer(data)` and it just works.

**Complete Visibility**: Every function call is tracked with inputs, outputs, timing, and errors. Perfect for debugging and optimization.

**Flexible Triggers**: Functions run when webhooks are called or on schedules. Build reactive systems that respond to events.

**Zero Infrastructure**: No servers to manage. Deploy with Docker and environment variables.

## Real-World Example: AI Document Processing System

```python
# Agent 1: Document Fetcher
def fetch_document(input):
    """Downloads and preprocesses documents"""
    return {"content": "...", "type": "pdf", "size": 1024}

# Agent 2: AI Analyzer  
def analyze_document(input):
    """Uses AI to extract structured data"""
    content = fetch_document({"url": input["url"]})
    # Run AI analysis...
    return {"entities": [...], "sentiment": "positive", "category": "invoice"}

# Agent 3: Business Validator
def validate_business_rules(input):
    """Checks compliance and business logic"""
    analysis = analyze_document(input)
    # Validate against rules...
    return {"valid": True, "risk_score": 0.2, "requires_review": False}

# Agent 4: Smart Router
def route_for_processing(input):
    """Intelligently routes based on analysis"""
    validation = validate_business_rules(input)
    
    if validation["requires_review"]:
        notify_human_reviewer({"document": input, "analysis": validation})
    else:
        auto_approve_document({"document": input, "validation": validation})
    
    return {"routed": True, "path": "auto_approved"}

# Webhook Handler: Orchestrator
def document_webhook(input):
    """Entry point that coordinates all agents"""
    result = route_for_processing({
        "url": input["body"]["document_url"],
        "user_id": input["headers"]["X-User-ID"]
    })
    return {"success": True, "processing": result}
```

**Trigger the system:**
```bash
curl -X POST https://your-app.com/api/v1/h/process-document \
  -H "X-User-ID: user123" \
  -d '{"document_url": "https://example.com/invoice.pdf"}'
```

**What happens automatically:**
1. `document_webhook` receives the request
2. Calls `route_for_processing` with extracted data
3. Which calls `validate_business_rules` 
4. Which calls `analyze_document`
5. Which calls `fetch_document`
6. Each function tracks execution, timing, and results
7. System makes intelligent routing decisions
8. Notifications sent if human review needed

## Technical Features

- 🚀 **Function Execution Engine**: Execute Python functions with AST injection for automatic tracking
- 🔗 **Webhook Integration**: Trigger functions via HTTP webhooks with dynamic routing
- ⏰ **Scheduled Jobs**: Run functions on cron schedules using APScheduler
- 📊 **Execution Tracking**: Complete audit trail of all function calls and executions
- 📦 **Package Management**: Install and manage Python packages for functions
- 🔄 **Function Versioning**: Track and rollback function versions
- 📋 **Real-time Logging**: Redis-based logging for real-time monitoring
- 🔍 **API-First Design**: Complete REST API for all operations

## Architecture

### Core Components

1. **Function Storage**: Python code stored as text in PostgreSQL with JSON schemas
2. **Execution Engine**: AST-based code injection for automatic tracking
3. **Webhook Handler**: Dynamic routing for webhook-triggered functions
4. **Scheduler**: APScheduler integration for cron-based execution
5. **Tracking System**: Automatic audit logging of all function calls
6. **Package Manager**: pip-based package installation and management

### Database Models

- **Function**: Store function code, schemas, and metadata
- **FunctionVersion**: Version history for functions
- **Webhook**: Webhook endpoint configurations
- **ScheduledJob**: Cron job definitions
- **Execution**: Main execution records
- **StepExecution**: Individual function call tracking
- **InstalledPackage**: Package installation records

## Quick Start

### Prerequisites

- Python 3.11+
- Poetry
- PostgreSQL
- Redis
- Census authentication service (for user management)

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd maestro
```

2. Install Poetry (if not already installed):
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

3. Install dependencies:
```bash
poetry install
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your database, Redis, and Census service URLs
```

**Important**: Configure Census service integration in `.env`:
```bash
CENSUS_API_URL=http://localhost:8001  # Your Census service URL
CENSUS_JWT_SECRET=your-jwt-secret     # Optional: for local JWT decoding
```

5. Set up the database:
```bash
poetry run python scripts/setup_db.py
```

6. Start the server:
```bash
poetry run uvicorn app.main:app --reload
```

7. Load example functions:
```bash
poetry run python scripts/example_functions.py
```

### Docker Deployment

```bash
# Build the image
docker build -t maestro .

# Run with environment variables
docker run -d \
  -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@host:5432/maestro \
  -e REDIS_URL=redis://host:6379/0 \
  -e CENSUS_API_URL=http://your-census-service:8001 \
  -e DEBUG=false \
  maestro
```

## Authentication & Multi-Tenancy

Maestro uses the Census service for authentication and multi-tenant access control:

### Authentication Flow
1. Users authenticate with Census service to get JWT tokens
2. All API requests must include `Authorization: Bearer <jwt-token>` header
3. Maestro validates tokens with Census and extracts user information
4. Each user is associated with a subtenant for data isolation

### Subtenant Management (Admin Only)
```bash
# Create a new subtenant
curl -X POST http://localhost:8000/api/v1/subtenants \
  -H "Authorization: Bearer <admin-jwt-token>" \
  -H "Content-Type: application/json" \
  -d '{"subtenant_id": "company-a", "description": "Company A Workspace"}'

# Grant user access to subtenant
curl -X POST http://localhost:8000/api/v1/subtenants/company-a/grant-access \
  -H "Authorization: Bearer <admin-jwt-token>" \
  -H "Content-Type: application/json" \
  -d '{"user_email": "user@company-a.com"}'
```

## Testing the Platform

### 1. Create a Function

**Example 1: Function returning a string**
```bash
curl -X POST http://localhost:8000/api/v1/functions \
  -H "Authorization: Bearer <your-jwt-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hello_world",
    "description": "Simple hello world function that returns a string",
    "code": "def hello_world(input):\n    return f\"Hello, {input.get(\"name\", \"World\")}!\"",
    "input_schema": {
      "type": "object",
      "properties": {
        "name": {"type": "string"}
      }
    },
    "output_schema": {
      "type": "string"
    }
  }'
```

**Example 2: Function returning an object**
```bash
curl -X POST http://localhost:8000/api/v1/functions \
  -H "Content-Type: application/json" \
  -d '{
    "name": "user_info",
    "description": "Function returning user information object",
    "code": "def user_info(input):\n    return {\"user\": input[\"name\"], \"age\": input[\"age\"], \"adult\": input[\"age\"] >= 18}",
    "input_schema": {
      "type": "object",
      "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"}
      },
      "required": ["name", "age"]
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "user": {"type": "string"},
        "age": {"type": "integer"},
        "adult": {"type": "boolean"}
      },
      "required": ["user", "age", "adult"]
    }
  }'
```

**Example 3: Function returning a boolean**
```bash
curl -X POST http://localhost:8000/api/v1/functions \
  -H "Content-Type: application/json" \
  -d '{
    "name": "is_even",
    "description": "Check if a number is even",
    "code": "def is_even(input):\n    return input[\"number\"] % 2 == 0",
    "input_schema": {
      "type": "object",
      "properties": {
        "number": {"type": "integer"}
      },
      "required": ["number"]
    },
    "output_schema": {
      "type": "boolean"
    }
  }'
```

### 2. Create a Webhook

```bash
curl -X POST http://localhost:8000/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "path": "hello",
    "function_name": "hello_world",
    "http_method": "POST"
  }'
```

### 3. Trigger via Webhook

```bash
curl -X POST http://localhost:8000/api/v1/h/hello \
  -H "Content-Type: application/json" \
  -d '{"name": "Claude"}'
```

### 4. Create a Schedule

```bash
curl -X POST http://localhost:8000/api/v1/schedules \
  -H "Content-Type: application/json" \
  -d '{
    "name": "daily-hello",
    "function_name": "hello_world",
    "cron_expression": "0 9 * * *",
    "input_data": {"name": "Daily User"}
  }'
```

### 5. Monitor Executions

```bash
# List all executions
curl http://localhost:8000/api/v1/executions

# Get specific execution details
curl http://localhost:8000/api/v1/executions/{execution_id}

# Get execution logs
curl http://localhost:8000/api/v1/executions/{execution_id}/logs

# Check scheduler status
curl http://localhost:8000/scheduler/status
```

## API Documentation

Once the server is running, visit:
- **Interactive API Docs**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### Main Endpoints

**Functions:**
- `POST /api/v1/functions` - Create function
- `GET /api/v1/functions` - List functions
- `GET /api/v1/functions/{name}` - Get function details
- `PUT /api/v1/functions/{name}` - Update function
- `DELETE /api/v1/functions/{name}` - Delete function
- `GET /api/v1/functions/{name}/versions` - Get version history
- `POST /api/v1/functions/{name}/rollback/{version}` - Rollback to version

**Webhooks:**
- `POST /api/v1/webhooks` - Create webhook
- `GET /api/v1/webhooks` - List webhooks
- `PUT /api/v1/webhooks/{id}` - Update webhook
- `DELETE /api/v1/webhooks/{id}` - Delete webhook

**Webhook Handler:**
- `* /api/v1/h/{path}` - Dynamic webhook handler (any HTTP method)

**Schedules:**
- `POST /api/v1/schedules` - Create scheduled job
- `GET /api/v1/schedules` - List scheduled jobs
- `PUT /api/v1/schedules/{id}` - Update scheduled job
- `DELETE /api/v1/schedules/{id}` - Delete scheduled job

**Executions:**
- `GET /api/v1/executions` - List executions
- `GET /api/v1/executions/{id}` - Get execution details
- `GET /api/v1/executions/{id}/steps` - Get execution steps
- `GET /api/v1/executions/{id}/logs` - Get execution logs

**Packages:**
- `POST /api/v1/packages` - Install package
- `GET /api/v1/packages` - List packages
- `DELETE /api/v1/packages/{name}` - Uninstall package

## Function Development

### Function Signature

All functions must follow this signature:
```python
def function_name(input):
    # input is validated against input_schema
    # return value is validated against output_schema
    return result  # Can be any type that matches output_schema
```

### Return Value Flexibility

Functions can return **any data type** that matches their `output_schema`. Here are examples:

```python
# Return a string
def get_greeting(input):
    return f"Hello, {input['name']}!"
# output_schema: {"type": "string"}

# Return a number
def calculate_total(input):
    return sum(input['numbers'])
# output_schema: {"type": "number"}

# Return a boolean
def is_valid_email(input):
    return "@" in input['email']
# output_schema: {"type": "boolean"}

# Return an array
def get_tags(input):
    return ["tag1", "tag2", "tag3"]
# output_schema: {"type": "array", "items": {"type": "string"}}

# Return an object/dict
def process_data(input):
    return {
        "status": "success",
        "count": len(input['items']),
        "processed_at": "2024-01-01T00:00:00Z"
    }
# output_schema: {
#   "type": "object",
#   "properties": {
#     "status": {"type": "string"},
#     "count": {"type": "integer"},
#     "processed_at": {"type": "string", "format": "date-time"}
#   },
#   "required": ["status", "count"]
# }
```

### Schema Definition

Use JSON Schema for input/output validation. The schema defines what data type and structure your function expects and returns:

**Input Schema Examples:**
```json
// Simple string input
{"type": "string"}

// Object with required properties
{
  "type": "object",
  "properties": {
    "name": {"type": "string"},
    "age": {"type": "integer", "minimum": 0}
  },
  "required": ["name"]
}

// Array of numbers
{
  "type": "array",
  "items": {"type": "number"}
}
```

**Output Schema Examples:**
```json
// Simple boolean return
{"type": "boolean"}

// Complex object return
{
  "type": "object",
  "properties": {
    "success": {"type": "boolean"},
    "data": {
      "type": "array",
      "items": {"type": "string"}
    },
    "timestamp": {"type": "string", "format": "date-time"}
  },
  "required": ["success"]
}
```

### Calling Other Functions

Functions can call other functions directly:
```python
def workflow_function(input):
    # Call another function
    result1 = helper_function({"data": input["value"]})
    
    # Call another function with result
    result2 = processor_function({"input": result1})
    
    return {"final_result": result2}
```

### Webhook Handler Functions

For webhook endpoints, create functions that handle request data:
```python
def webhook_handler(input):
    # Extract data from webhook request
    body_data = input["body"]
    headers = input["headers"]
    query_params = input["query"]
    
    # Process the request
    result = process_data(body_data)
    
    # Return response
    return {"success": True, "data": result}
```

### Automatic Tracking

All function calls are automatically tracked with:
- Start/end timestamps
- Input/output data
- Error handling and stack traces
- Duration measurement
- Call stack tracking for nested calls

## Example: Invoice Processing Workflow

The platform includes a complete invoice processing example:

### 1. Helper Functions
- `fetch_document(input)` - Download documents from URLs
- `extract_invoice_data(input)` - Parse invoice data using regex
- `validate_customer(input)` - Validate customer information
- `notify_team(input)` - Send team notifications

### 2. Main Workflow
```python
def process_invoice(input):
    # Fetch document
    doc = fetch_document({"url": input["document_url"]})
    
    # Extract data
    invoice_data = extract_invoice_data({"content": doc["content"]})
    
    # Validate customer
    validation = validate_customer({
        "customer_id": input["customer_id"],
        "invoice_data": invoice_data
    })
    
    # Notify for high priority
    if input.get("priority") == "high":
        notify_team({"message": f"High priority invoice: {invoice_data['number']}"})
    
    return {
        "invoice_number": invoice_data["number"],
        "total": invoice_data["total"],
        "customer_valid": validation["is_valid"],
        "status": "processed"
    }
```

### 3. Webhook Handler
```python
def invoice_webhook_handler(input):
    # Extract webhook data
    document_url = input["body"].get("url")
    customer_id = input["headers"].get("X-Customer-ID", "unknown")
    priority = input["query"].get("priority", "normal")
    
    # Call main workflow
    result = process_invoice({
        "document_url": document_url,
        "customer_id": customer_id,
        "priority": priority
    })
    
    return {"success": True, "invoice": result}
```

### 4. Test the Workflow
```bash
curl -X POST http://localhost:8000/api/v1/h/process-invoice \
  -H 'Content-Type: application/json' \
  -H 'X-Customer-ID: CUST001' \
  -d '{"url": "https://example.com/invoice.txt", "priority": "high"}'
```

## Configuration

### Environment Variables

- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `DEBUG`: Enable debug mode (true/false)
- `FUNCTION_TIMEOUT`: Function execution timeout in seconds
- `ALLOW_PACKAGE_INSTALLATION`: Enable package installation (true/false)

### Settings File

Configuration is managed in `app/core/config.py` using Pydantic settings.

## Monitoring & Observability

### Execution Tracking

Every function execution creates:
1. **Execution Record**: Main execution with status, timing, and results
2. **Step Records**: Individual function calls within the execution
3. **Redis Logs**: Real-time event stream for monitoring

### Real-time Logging

Redis Streams provide real-time logging with:
- Execution start/end events
- Function call tracking
- Error and exception logging
- Performance metrics

### Monitoring Endpoints

- `GET /health` - Health check
- `GET /scheduler/status` - Scheduler status and active jobs
- `GET /api/v1/executions` - Execution history with filtering
- `GET /api/v1/executions/{id}/logs` - Real-time execution logs

## Development

### Project Structure

```
maestro/
├── app/
│   ├── api/           # FastAPI routes and request/response schemas
│   │   ├── functions.py    # Function CRUD operations
│   │   ├── webhooks.py     # Webhook management
│   │   ├── webhook_handler.py  # Dynamic webhook routing
│   │   ├── schedules.py    # Scheduled job management
│   │   ├── executions.py   # Execution monitoring
│   │   └── packages.py     # Package management
│   ├── core/          # Configuration and database setup
│   │   ├── config.py       # Application settings
│   │   └── database.py     # Database connection and sessions
│   ├── models/        # SQLAlchemy ORM models
│   │   ├── function.py     # Function and version models
│   │   ├── webhook.py      # Webhook model
│   │   ├── schedule.py     # Scheduled job model
│   │   ├── execution.py    # Execution tracking models
│   │   └── package.py      # Package management model
│   ├── services/      # Business logic services
│   │   ├── execution_engine.py  # Function execution with AST injection
│   │   ├── tracking.py     # Execution step tracking
│   │   ├── scheduler.py    # APScheduler integration
│   │   └── redis_logger.py # Redis Streams logging
│   └── utils/         # Utility functions
├── migrations/        # Alembic database migrations
├── scripts/          # Setup and utility scripts
│   ├── setup_db.py        # Database initialization
│   └── example_functions.py  # Load example functions
├── tests/            # Test suite
├── pyproject.toml    # Poetry configuration and dependencies
├── poetry.lock       # Locked dependency versions
├── Dockerfile       # Production container
└── .env.example     # Environment variables template
```

### Running Tests

```bash
# Install with dev dependencies (if not already done)
poetry install

# Run tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=app
```

### Code Quality

```bash
# Format code
poetry run black .

# Lint code
poetry run ruff check .

# Type checking
poetry run mypy .
```

## Production Deployment

### Docker Deployment

```bash
# Build image
docker build -t maestro .

# Run with environment variables
docker run -d \
  -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@host:5432/maestro \
  -e REDIS_URL=redis://host:6379/0 \
  -e DEBUG=false \
  -e FUNCTION_TIMEOUT=300 \
  -e ALLOW_PACKAGE_INSTALLATION=true \
  maestro

# Or run interactively for development
docker run -it \
  -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@host:5432/maestro \
  -e REDIS_URL=redis://host:6379/0 \
  -e DEBUG=true \
  maestro
```

### Environment Setup

1. **Database Setup:**
   - PostgreSQL 12+ with connection pooling
   - Run migrations: `alembic upgrade head`

2. **Redis Setup:**
   - Redis 6+ for logging and caching
   - Configure persistence for job store

3. **Application Configuration:**
   - Set production environment variables
   - Configure logging and monitoring
   - Set up reverse proxy (nginx/traefik)

### Security Considerations

- ✅ Enable authentication for webhooks (`requires_auth: true`)
- ✅ Validate all function inputs with JSON Schema
- ✅ Limit package installation in production environments
- ✅ Monitor function execution times and resource usage
- ✅ Set up proper logging and error tracking
- ✅ Use environment variables for sensitive configuration
- ✅ Run with non-root user in containers

### Performance Tuning

- **Database**: Connection pooling, query optimization
- **Redis**: Memory optimization, persistence configuration  
- **Functions**: Timeout settings, resource limits
- **Scheduler**: Job concurrency, queue management

## Troubleshooting

### Common Issues

1. **Database Connection Errors**
   ```bash
   # Check connection
   psql $DATABASE_URL -c "SELECT 1;"
   
   # Check if database exists
   createdb maestro  # if database doesn't exist
   ```

2. **Redis Connection Errors**
   ```bash
   # Check Redis is running
   redis-cli ping
   
   # Test connection with URL
   redis-cli -u $REDIS_URL ping
   ```

3. **Function Execution Errors**
   ```bash
   # Check execution logs
   curl http://localhost:8000/api/v1/executions/{execution_id}/logs
   
   # Check function code and schemas
   curl http://localhost:8000/api/v1/functions/{function_name}
   ```

4. **Scheduler Issues**
   ```bash
   # Check scheduler status
   curl http://localhost:8000/scheduler/status
   
   # Check scheduled jobs
   curl http://localhost:8000/api/v1/schedules
   ```

### Debug Mode

Enable debug mode for detailed logging:
```bash
export DEBUG=true
poetry run uvicorn app.main:app --reload --log-level debug
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make changes with tests
4. Run code quality checks (`poetry run black .`, `poetry run ruff check .`, `poetry run mypy .`)
5. Submit a pull request

### Development Guidelines

- Follow PEP 8 style guidelines
- Add type hints to all functions
- Write tests for new features
- Update documentation for API changes
- Use conventional commit messages

## License

MIT License - see LICENSE file for details.

## Support

- 📖 **Documentation**: http://localhost:8000/docs
- 🐛 **Issues**: Create GitHub issues for bugs
- 💡 **Features**: Submit feature requests via GitHub issues
- 💬 **Discussions**: Use GitHub discussions for questions
