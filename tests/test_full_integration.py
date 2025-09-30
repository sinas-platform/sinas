"""
Comprehensive integration test script for Maestro Cloud Functions Platform.
Tests the entire application functionality from API endpoints to execution.
"""

import asyncio
import httpx
import json
import time
import uuid
from typing import Dict, Any, Optional
from datetime import datetime, timedelta


class MaestroTestClient:
    """Test client for Maestro API with authentication support."""
    
    def __init__(self, base_url: str = "http://localhost:8000", auth_token: Optional[str] = None):
        self.base_url = base_url
        self.auth_token = auth_token
        self.headers = {"Content-Type": "application/json"}
        if auth_token:
            self.headers["Authorization"] = f"Bearer {auth_token}"
    
    async def request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        """Make an authenticated request to the API."""
        async with httpx.AsyncClient() as client:
            url = f"{self.base_url}{endpoint}"
            response = await client.request(method, url, headers=self.headers, **kwargs)
            return response
    
    async def get(self, endpoint: str, **kwargs) -> httpx.Response:
        return await self.request("GET", endpoint, **kwargs)
    
    async def post(self, endpoint: str, **kwargs) -> httpx.Response:
        return await self.request("POST", endpoint, **kwargs)
    
    async def put(self, endpoint: str, **kwargs) -> httpx.Response:
        return await self.request("PUT", endpoint, **kwargs)
    
    async def delete(self, endpoint: str, **kwargs) -> httpx.Response:
        return await self.request("DELETE", endpoint, **kwargs)


class MaestroIntegrationTest:
    """Comprehensive integration test suite for Maestro."""
    
    def __init__(self, admin_token: str, user_token: str, base_url: str = "http://localhost:8000"):
        self.admin_client = MaestroTestClient(base_url, admin_token)
        self.user_client = MaestroTestClient(base_url, user_token)
        self.subtenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"
        self.test_results = []
    
    def log_test(self, test_name: str, success: bool, message: str = ""):
        """Log test result."""
        status = "✅ PASS" if success else "❌ FAIL"
        self.test_results.append(f"{status} {test_name}: {message}")
        print(f"{status} {test_name}: {message}")
    
    async def run_all_tests(self):
        """Run all integration tests."""
        print("🚀 Starting Maestro Integration Tests\n")
        
        try:
            # Test 1: Subtenant Management
            await self.test_subtenant_management()
            
            # Test 2: Function Management
            await self.test_function_management()
            
            # Test 3: Package Management
            await self.test_package_management()
            
            # Test 4: Webhook Management
            await self.test_webhook_management()
            
            # Test 5: Schedule Management
            await self.test_schedule_management()
            
            # Test 6: Function Execution
            await self.test_function_execution()
            
            # Test 7: Function Chaining
            await self.test_function_chaining()
            
            # Test 8: Webhook Triggering
            await self.test_webhook_triggering()
            
            # Test 9: Execution Tracking
            await self.test_execution_tracking()
            
        except Exception as e:
            self.log_test("CRITICAL ERROR", False, str(e))
        
        # Print summary
        print("\n" + "="*60)
        print("TEST SUMMARY")
        print("="*60)
        for result in self.test_results:
            print(result)
        
        passed = sum(1 for r in self.test_results if "✅ PASS" in r)
        total = len(self.test_results)
        print(f"\nPassed: {passed}/{total}")
        
        if passed == total:
            print("🎉 All tests passed!")
        else:
            print("⚠️  Some tests failed. Check logs above.")
    
    async def test_subtenant_management(self):
        """Test subtenant creation and management."""
        print("\n📋 Testing Subtenant Management...")
        
        # Create subtenant
        response = await self.admin_client.post("/api/v1/subtenants", json={
            "subtenant_id": self.subtenant_id,
            "description": "Test subtenant for integration testing"
        })
        
        if response.status_code == 200:
            self.log_test("Create Subtenant", True)
        else:
            self.log_test("Create Subtenant", False, f"Status: {response.status_code}")
            return
        
        # List subtenants
        response = await self.admin_client.get("/api/v1/subtenants")
        if response.status_code == 200:
            subtenants = response.json()
            if any(s["subtenant_id"] == self.subtenant_id for s in subtenants):
                self.log_test("List Subtenants", True)
            else:
                self.log_test("List Subtenants", False, "Created subtenant not found")
        else:
            self.log_test("List Subtenants", False, f"Status: {response.status_code}")
        
        # Grant user access to subtenant
        response = await self.admin_client.post(
            f"/api/v1/subtenants/{self.subtenant_id}/grant-access",
            json={"user_email": "test@example.com"}
        )
        
        # Note: This might fail if Census service is not running
        if response.status_code in [200, 503]:  # 503 = Census unavailable
            self.log_test("Grant User Access", True, "Census integration working or expected unavailable")
        else:
            self.log_test("Grant User Access", False, f"Status: {response.status_code}")
    
    async def test_function_management(self):
        """Test function CRUD operations."""
        print("\n⚙️ Testing Function Management...")
        
        # Create a simple function
        function_data = {
            "name": "hello_world",
            "description": "Simple hello world function",
            "code": '''def hello_world(input):
    name = input.get("name", "World")
    return f"Hello, {name}!"''',
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                }
            },
            "output_schema": {
                "type": "string"
            }
        }
        
        response = await self.user_client.post("/api/v1/functions", json=function_data)
        if response.status_code == 200:
            self.log_test("Create Function", True)
            function_id = response.json()["id"]
        else:
            self.log_test("Create Function", False, f"Status: {response.status_code}")
            return
        
        # List functions
        response = await self.user_client.get("/api/v1/functions")
        if response.status_code == 200:
            functions = response.json()
            if any(f["name"] == "hello_world" for f in functions):
                self.log_test("List Functions", True)
            else:
                self.log_test("List Functions", False, "Created function not found")
        else:
            self.log_test("List Functions", False, f"Status: {response.status_code}")
        
        # Get specific function
        response = await self.user_client.get(f"/api/v1/functions/{function_id}")
        if response.status_code == 200:
            self.log_test("Get Function", True)
        else:
            self.log_test("Get Function", False, f"Status: {response.status_code}")
        
        # Update function
        updated_data = function_data.copy()
        updated_data["description"] = "Updated hello world function"
        
        response = await self.user_client.put(f"/api/v1/functions/{function_id}", json=updated_data)
        if response.status_code == 200:
            self.log_test("Update Function", True)
        else:
            self.log_test("Update Function", False, f"Status: {response.status_code}")
    
    async def test_package_management(self):
        """Test package installation and management."""
        print("\n📦 Testing Package Management...")
        
        # Install a package
        response = await self.user_client.post("/api/v1/packages", json={
            "package_name": "requests"
        })
        
        if response.status_code == 200:
            self.log_test("Install Package", True)
        else:
            self.log_test("Install Package", False, f"Status: {response.status_code}")
        
        # List packages
        response = await self.user_client.get("/api/v1/packages")
        if response.status_code == 200:
            packages = response.json()
            if any(p["package_name"] == "requests" for p in packages):
                self.log_test("List Packages", True)
            else:
                self.log_test("List Packages", False, "Installed package not found")
        else:
            self.log_test("List Packages", False, f"Status: {response.status_code}")
    
    async def test_webhook_management(self):
        """Test webhook creation and management."""
        print("\n🔗 Testing Webhook Management...")
        
        # Create webhook
        webhook_data = {
            "name": "test_webhook",
            "description": "Test webhook for integration testing",
            "path": "/test-webhook",
            "function_name": "hello_world",
            "is_active": True
        }
        
        response = await self.user_client.post("/api/v1/webhooks", json=webhook_data)
        if response.status_code == 200:
            self.log_test("Create Webhook", True)
            webhook_id = response.json()["id"]
        else:
            self.log_test("Create Webhook", False, f"Status: {response.status_code}")
            return
        
        # List webhooks
        response = await self.user_client.get("/api/v1/webhooks")
        if response.status_code == 200:
            webhooks = response.json()
            if any(w["name"] == "test_webhook" for w in webhooks):
                self.log_test("List Webhooks", True)
            else:
                self.log_test("List Webhooks", False, "Created webhook not found")
        else:
            self.log_test("List Webhooks", False, f"Status: {response.status_code}")
    
    async def test_schedule_management(self):
        """Test scheduled job creation and management."""
        print("\n⏰ Testing Schedule Management...")
        
        # Create schedule
        schedule_data = {
            "name": "test_schedule",
            "description": "Test schedule for integration testing",
            "function_name": "hello_world",
            "cron_expression": "0 */6 * * *",  # Every 6 hours
            "input_data": {"name": "Scheduled"},
            "is_active": False  # Don't actually run it
        }
        
        response = await self.user_client.post("/api/v1/schedules", json=schedule_data)
        if response.status_code == 200:
            self.log_test("Create Schedule", True)
            schedule_id = response.json()["id"]
        else:
            self.log_test("Create Schedule", False, f"Status: {response.status_code}")
            return
        
        # List schedules
        response = await self.user_client.get("/api/v1/schedules")
        if response.status_code == 200:
            schedules = response.json()
            if any(s["name"] == "test_schedule" for s in schedules):
                self.log_test("List Schedules", True)
            else:
                self.log_test("List Schedules", False, "Created schedule not found")
        else:
            self.log_test("List Schedules", False, f"Status: {response.status_code}")
    
    async def test_function_execution(self):
        """Test direct function execution."""
        print("\n🚀 Testing Function Execution...")
        
        # Execute function
        response = await self.user_client.post("/api/v1/executions", json={
            "function_name": "hello_world",
            "input_data": {"name": "Integration Test"},
            "trigger_type": "MANUAL"
        })
        
        if response.status_code == 200:
            execution_data = response.json()
            execution_id = execution_data["execution_id"]
            self.log_test("Execute Function", True, f"Execution ID: {execution_id}")
            
            # Wait a bit for execution to complete
            await asyncio.sleep(2)
            
            # Check execution status
            response = await self.user_client.get(f"/api/v1/executions/{execution_id}")
            if response.status_code == 200:
                execution = response.json()
                if execution["status"] == "COMPLETED":
                    self.log_test("Function Execution Complete", True, f"Output: {execution.get('output_data')}")
                else:
                    self.log_test("Function Execution Complete", False, f"Status: {execution['status']}")
            else:
                self.log_test("Check Execution Status", False, f"Status: {response.status_code}")
        else:
            self.log_test("Execute Function", False, f"Status: {response.status_code}")
    
    async def test_function_chaining(self):
        """Test function chaining capabilities."""
        print("\n🔗 Testing Function Chaining...")
        
        # Create a function that calls another function
        chained_function = {
            "name": "greeting_chain",
            "description": "Function that calls hello_world",
            "code": '''def greeting_chain(input):
    # Call the hello_world function
    result = hello_world({"name": input.get("name", "Chained User")})
    return {"greeting": result, "timestamp": "2024-01-01T00:00:00Z"}''',
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                }
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "greeting": {"type": "string"},
                    "timestamp": {"type": "string"}
                }
            }
        }
        
        response = await self.user_client.post("/api/v1/functions", json=chained_function)
        if response.status_code == 200:
            self.log_test("Create Chained Function", True)
            
            # Execute the chained function
            response = await self.user_client.post("/api/v1/executions", json={
                "function_name": "greeting_chain",
                "input_data": {"name": "Chain Test"},
                "trigger_type": "MANUAL"
            })
            
            if response.status_code == 200:
                execution_id = response.json()["execution_id"]
                await asyncio.sleep(3)  # Wait longer for chained execution
                
                # Check result
                response = await self.user_client.get(f"/api/v1/executions/{execution_id}")
                if response.status_code == 200:
                    execution = response.json()
                    if execution["status"] == "COMPLETED":
                        self.log_test("Execute Chained Function", True, f"Output: {execution.get('output_data')}")
                    else:
                        self.log_test("Execute Chained Function", False, f"Status: {execution['status']}")
                else:
                    self.log_test("Check Chained Execution", False, f"Status: {response.status_code}")
            else:
                self.log_test("Execute Chained Function", False, f"Status: {response.status_code}")
        else:
            self.log_test("Create Chained Function", False, f"Status: {response.status_code}")
    
    async def test_webhook_triggering(self):
        """Test webhook triggering functionality."""
        print("\n🎯 Testing Webhook Triggering...")
        
        # Trigger webhook
        response = await self.user_client.post("/webhooks/test-webhook", json={
            "name": "Webhook Test"
        })
        
        if response.status_code == 200:
            execution_data = response.json()
            execution_id = execution_data.get("execution_id")
            self.log_test("Trigger Webhook", True, f"Execution ID: {execution_id}")
            
            if execution_id:
                await asyncio.sleep(2)
                
                # Check webhook execution
                response = await self.user_client.get(f"/api/v1/executions/{execution_id}")
                if response.status_code == 200:
                    execution = response.json()
                    if execution["status"] == "COMPLETED":
                        self.log_test("Webhook Execution Complete", True)
                    else:
                        self.log_test("Webhook Execution Complete", False, f"Status: {execution['status']}")
                else:
                    self.log_test("Check Webhook Execution", False, f"Status: {response.status_code}")
        else:
            self.log_test("Trigger Webhook", False, f"Status: {response.status_code}")
    
    async def test_execution_tracking(self):
        """Test execution tracking and monitoring."""
        print("\n📊 Testing Execution Tracking...")
        
        # List all executions
        response = await self.user_client.get("/api/v1/executions")
        if response.status_code == 200:
            executions = response.json()
            if len(executions) > 0:
                self.log_test("List Executions", True, f"Found {len(executions)} executions")
                
                # Test execution filtering
                first_execution = executions[0]
                execution_id = first_execution["execution_id"]
                
                # Get execution steps
                response = await self.user_client.get(f"/api/v1/executions/{execution_id}/steps")
                if response.status_code == 200:
                    steps = response.json()
                    self.log_test("Get Execution Steps", True, f"Found {len(steps)} steps")
                else:
                    self.log_test("Get Execution Steps", False, f"Status: {response.status_code}")
            else:
                self.log_test("List Executions", False, "No executions found")
        else:
            self.log_test("List Executions", False, f"Status: {response.status_code}")
    
    async def cleanup(self):
        """Clean up test data."""
        print("\n🧹 Cleaning up test data...")
        
        try:
            # Delete test subtenant (this will cascade delete all data)
            response = await self.admin_client.delete(f"/api/v1/subtenants/{self.subtenant_id}")
            if response.status_code == 200:
                self.log_test("Cleanup Subtenant", True)
            else:
                self.log_test("Cleanup Subtenant", False, f"Status: {response.status_code}")
        except Exception as e:
            self.log_test("Cleanup", False, str(e))


async def main():
    """Main test runner."""
    # Configuration
    BASE_URL = "http://localhost:8000"
    
    # You need to provide real JWT tokens from Census service
    # For testing purposes, you might need to:
    # 1. Start Census service
    # 2. Create test users
    # 3. Get JWT tokens for admin and regular user
    
    ADMIN_TOKEN = "your-admin-jwt-token-here"  # Replace with real admin token
    USER_TOKEN = "your-user-jwt-token-here"    # Replace with real user token
    
    # Check if tokens are provided
    if ADMIN_TOKEN == "your-admin-jwt-token-here" or USER_TOKEN == "your-user-jwt-token-here":
        print("❌ Please update the JWT tokens in the script before running tests")
        print("   You need to:")
        print("   1. Start the Census authentication service")
        print("   2. Create admin and regular user accounts")
        print("   3. Get JWT tokens for both users")
        print("   4. Update ADMIN_TOKEN and USER_TOKEN in this script")
        return
    
    # Run tests
    test_suite = MaestroIntegrationTest(ADMIN_TOKEN, USER_TOKEN, BASE_URL)
    
    try:
        await test_suite.run_all_tests()
    finally:
        # Always try to cleanup
        await test_suite.cleanup()


if __name__ == "__main__":
    asyncio.run(main())