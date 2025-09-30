"""
Interactive test script for Maestro with Census OTP authentication.
This script handles the OTP login flow and runs integration tests.
"""

import asyncio
import httpx
import json
from typing import Dict, Any, Optional


class CensusAuthenticator:
    """Handles Census OTP authentication flow."""
    
    def __init__(self, census_url: str = "http://localhost:8002"):
        self.census_url = census_url
    
    async def login_with_otp(self, email: str) -> str:
        """Login with email and OTP, returns JWT token."""
        async with httpx.AsyncClient() as client:
            # Step 1: Request OTP
            print(f"🔐 Requesting OTP for {email}...")
            response = await client.post(
                f"{self.census_url}/api/v1/auth/login",
                json={"email": email}
            )
            
            if response.status_code != 200:
                raise Exception(f"Login request failed: {response.status_code} - {response.text}")
            
            login_data = response.json()
            session_id = login_data["session_id"]
            print(f"✅ OTP sent! Session ID: {session_id}")
            
            # Step 2: Get OTP from user
            try:
                otp_code = input("📱 Enter the OTP code from your email: ").strip()
            except EOFError:
                print("⚠️  Cannot get interactive input. Please provide OTP manually.")
                print("You need to run this script in an interactive terminal.")
                raise Exception("Interactive input required for OTP")
            
            # Step 3: Verify OTP
            print("🔓 Verifying OTP...")
            response = await client.post(
                f"{self.census_url}/api/v1/auth/verify-otp",
                json={
                    "session_id": session_id,
                    "otp_code": otp_code
                }
            )
            
            if response.status_code != 200:
                raise Exception(f"OTP verification failed: {response.status_code} - {response.text}")
            
            verify_data = response.json()
            token = verify_data["access_token"]
            user = verify_data["user"]
            
            print(f"✅ Authentication successful!")
            print(f"   User ID: {user['id']}")
            print(f"   Email: {user['email']}")
            print(f"   Token: {token[:20]}...")
            
            return token
    
    async def check_admin_status(self, token: str, user_id: str) -> bool:
        """Check if user is admin."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.census_url}/api/v1/users/{user_id}/groups",
                headers={"Authorization": f"Bearer {token}"}
            )
            
            if response.status_code == 200:
                groups = response.json()
                for group in groups:
                    if group.get("group_name") == "Admins" and group.get("active"):
                        return True
            return False


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


class InteractiveMaestroTest:
    """Interactive integration test with OTP authentication."""
    
    def __init__(self):
        self.authenticator = CensusAuthenticator()
        self.admin_client = None
        self.user_client = None
        self.subtenant_id = None
        self.test_results = []
    
    def log_test(self, test_name: str, success: bool, message: str = ""):
        """Log test result."""
        status = "✅ PASS" if success else "❌ FAIL"
        self.test_results.append(f"{status} {test_name}: {message}")
        print(f"{status} {test_name}: {message}")
    
    async def setup_authentication(self):
        """Setup authentication for both admin and user."""
        print("\n🔑 Setting up authentication...")
        
        # Admin login
        admin_email = "kjeld.oostra@pulsr.io"
        print(f"\n👑 Admin login for {admin_email}")
        try:
            admin_token = await self.authenticator.login_with_otp(admin_email)
            self.admin_client = MaestroTestClient(auth_token=admin_token)
            
            # Get user info from token to check admin status
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "http://localhost:8002/api/v1/users/me",
                    headers={"Authorization": f"Bearer {admin_token}"}
                )
                if response.status_code == 200:
                    user_data = response.json()
                    user_id = user_data["id"]
                    is_admin = await self.authenticator.check_admin_status(admin_token, user_id)
                    if is_admin:
                        print("✅ Admin authentication successful")
                    else:
                        print("⚠️  User authenticated but not in Admins group")
            
        except Exception as e:
            print(f"❌ Admin authentication failed: {e}")
            return False
        
        # For this test, we'll use the same user as both admin and regular user
        # In a real scenario, you'd have separate accounts
        self.user_client = MaestroTestClient(auth_token=admin_token)
        print("✅ User client setup (using admin token)")
        
        return True
    
    async def test_subtenant_creation(self):
        """Create a test subtenant."""
        print("\n📋 Creating test subtenant...")
        
        import uuid
        self.subtenant_id = f"test-{uuid.uuid4().hex[:8]}"
        
        response = await self.admin_client.request("POST", "/api/v1/subtenants", json={
            "subtenant_id": self.subtenant_id,
            "description": "Interactive test subtenant"
        })
        
        if response.status_code == 200:
            self.log_test("Create Subtenant", True, f"ID: {self.subtenant_id}")
            return True
        else:
            self.log_test("Create Subtenant", False, f"Status: {response.status_code}")
            print(f"Response: {response.text}")
            return False
    
    async def test_function_creation_and_execution(self):
        """Test creating and executing a function."""
        print("\n⚙️ Testing function creation and execution...")
        
        # Create function
        function_data = {
            "name": "test_function",
            "description": "Interactive test function",
            "code": '''def test_function(input):
    name = input.get("name", "World")
    count = input.get("count", 1)
    return {"message": f"Hello {name}!", "executed_count": count}''',
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "count": {"type": "integer"}
                }
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "executed_count": {"type": "integer"}
                }
            }
        }
        
        response = await self.user_client.request("POST", "/api/v1/functions", json=function_data)
        if response.status_code == 200:
            self.log_test("Create Function", True)
            function_id = response.json()["id"]
        else:
            self.log_test("Create Function", False, f"Status: {response.status_code}")
            print(f"Response: {response.text}")
            return False
        
        # Execute function
        response = await self.user_client.request("POST", "/api/v1/executions", json={
            "function_name": "test_function",
            "input_data": {"name": "Interactive Test", "count": 42},
            "trigger_type": "MANUAL"
        })
        
        if response.status_code == 200:
            execution_data = response.json()
            execution_id = execution_data["execution_id"]
            self.log_test("Execute Function", True, f"Execution ID: {execution_id}")
            
            # Wait and check result
            await asyncio.sleep(3)
            response = await self.user_client.request("GET", f"/api/v1/executions/{execution_id}")
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
            print(f"Response: {response.text}")
    
    async def test_webhook_creation(self):
        """Test webhook creation."""
        print("\n🔗 Testing webhook creation...")
        
        webhook_data = {
            "name": "interactive_test_webhook",
            "description": "Webhook for interactive testing",
            "path": "/interactive-test",
            "function_name": "test_function",
            "is_active": True
        }
        
        response = await self.user_client.request("POST", "/api/v1/webhooks", json=webhook_data)
        if response.status_code == 200:
            webhook_id = response.json()["id"]
            self.log_test("Create Webhook", True, f"ID: {webhook_id}")
            
            # Test webhook trigger
            response = await self.user_client.request("POST", "/webhooks/interactive-test", json={
                "name": "Webhook Test", 
                "count": 99
            })
            
            if response.status_code == 200:
                self.log_test("Trigger Webhook", True)
            else:
                self.log_test("Trigger Webhook", False, f"Status: {response.status_code}")
        else:
            self.log_test("Create Webhook", False, f"Status: {response.status_code}")
            print(f"Response: {response.text}")
    
    async def cleanup(self):
        """Clean up test data."""
        print("\n🧹 Cleaning up...")
        
        if self.subtenant_id and self.admin_client:
            response = await self.admin_client.request("DELETE", f"/api/v1/subtenants/{self.subtenant_id}")
            if response.status_code == 200:
                self.log_test("Cleanup Subtenant", True)
            else:
                self.log_test("Cleanup Subtenant", False, f"Status: {response.status_code}")
    
    async def run_interactive_tests(self):
        """Run the interactive test suite."""
        print("🚀 Maestro Interactive Integration Test")
        print("=" * 50)
        print("This test requires Census OTP authentication.")
        print("Make sure Census is running on localhost:8002")
        print("=" * 50)
        
        try:
            # Setup authentication
            if not await self.setup_authentication():
                print("❌ Authentication setup failed. Cannot proceed.")
                return
            
            # Run tests
            if await self.test_subtenant_creation():
                await self.test_function_creation_and_execution()
                await self.test_webhook_creation()
            
        except Exception as e:
            self.log_test("CRITICAL ERROR", False, str(e))
        
        finally:
            # Always try cleanup
            await self.cleanup()
        
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


async def main():
    """Run interactive tests."""
    print("Starting interactive Maestro tests with Census OTP authentication...\n")
    
    # Check prerequisites
    print("📋 Prerequisites:")
    print("  ✓ Census service running on localhost:8002")
    print("  ✓ Maestro service running on localhost:8000")
    print("  ✓ Database and Redis accessible")
    print("  ✓ Email access for OTP codes")
    
    print("\n▶️  Proceeding with tests automatically...")
    print("You will be prompted for OTP when needed.")
    
    test_suite = InteractiveMaestroTest()
    await test_suite.run_interactive_tests()


if __name__ == "__main__":
    asyncio.run(main())