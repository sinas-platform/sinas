# Maestro Integration Tests

This directory contains comprehensive test scripts for the Maestro Cloud Functions Platform.

## Test Scripts

### 1. `test_basic_functionality.py` 
**Basic server functionality test (no authentication required)**

Tests basic server health and API endpoint structure without requiring authentication tokens.

```bash
# Start Maestro server first
poetry run uvicorn app.main:app --reload

# Run basic tests in another terminal
cd tests
python test_basic_functionality.py
```

**What it tests:**
- ✅ Server is running and responding
- ✅ API endpoints exist and return expected auth errors
- ✅ OpenAPI schema generation
- ✅ Webhook endpoint structure
- ✅ CORS configuration

### 2. `test_full_integration.py`
**Complete end-to-end integration test (requires authentication)**

Comprehensive test suite that tests all functionality from API to execution.

```bash
# Prerequisites:
# 1. Start Census authentication service
# 2. Start Maestro server 
# 3. Get JWT tokens for admin and regular user
# 4. Update tokens in the script

# Edit the script first:
# ADMIN_TOKEN = "your-real-admin-jwt-token"
# USER_TOKEN = "your-real-user-jwt-token"

python test_full_integration.py
```

**What it tests:**
- 🏗️ **Subtenant Management**: Create, list, grant access
- ⚙️ **Function Management**: CRUD operations for functions
- 📦 **Package Management**: Install and list Python packages
- 🔗 **Webhook Management**: Create and configure webhooks
- ⏰ **Schedule Management**: Create and manage scheduled jobs
- 🚀 **Function Execution**: Direct function execution via API
- 🔗 **Function Chaining**: Functions calling other functions
- 🎯 **Webhook Triggering**: Trigger functions via webhooks
- 📊 **Execution Tracking**: Monitor and track executions

## Test Setup

### Without Authentication (Basic Tests)
1. Start Maestro server:
   ```bash
   poetry run uvicorn app.main:app --reload
   ```

2. Run basic tests:
   ```bash
   python tests/test_basic_functionality.py
   ```

### With Authentication (Full Integration)
1. Start Census authentication service on port 8001

2. Create test users in Census:
   - Admin user (member of "Admins" group)
   - Regular user

3. Get JWT tokens for both users

4. Start Maestro server:
   ```bash
   poetry run uvicorn app.main:app --reload
   ```

5. Update tokens in `test_full_integration.py`

6. Run full tests:
   ```bash
   python tests/test_full_integration.py
   ```

## Expected Results

### Basic Tests
- All tests should pass if server is running
- 401/403 errors are expected (indicates auth is working)
- Server health check should return 200

### Full Integration Tests  
- All 20+ tests should pass with proper authentication
- Tests create and clean up their own data
- Uses a temporary subtenant for isolation

## Troubleshooting

### "Connection refused" errors
- Make sure Maestro server is running on port 8000
- Check database and Redis are running

### "Invalid or expired token" errors  
- Update JWT tokens in the test script
- Make sure Census service is running
- Verify tokens are not expired

### "Census service unavailable" errors
- Start Census authentication service
- Check `CENSUS_API_URL` in Maestro configuration

### Database errors
- Run migrations: `poetry run alembic upgrade head`
- Check PostgreSQL is running and accessible

## Test Data

The integration tests create temporary data:
- Subtenant with random ID
- Test functions (`hello_world`, `greeting_chain`)
- Test webhook and schedule
- All data is cleaned up after tests

Test data is isolated by subtenant, so multiple test runs won't interfere with each other.