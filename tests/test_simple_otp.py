#!/usr/bin/env python3
"""
Simple Maestro test with proper Census OTP authentication.
Run this in your terminal to provide OTP input.
JWT token is cached to avoid repeated OTP requests.
"""

import asyncio
import httpx
import sys
import json
import os
from datetime import datetime, timedelta


def load_cached_token():
    """Load cached JWT token if valid."""
    cache_file = "/tmp/maestro_jwt_cache.json"
    if not os.path.exists(cache_file):
        return None
    
    try:
        with open(cache_file, 'r') as f:
            cache = json.load(f)
        
        # Check if token is expired (with 5 minute buffer)
        expires_at = datetime.fromisoformat(cache['expires_at'])
        if datetime.now() + timedelta(minutes=5) < expires_at:
            print(f"🔄 Using cached token (expires: {expires_at.strftime('%H:%M:%S')})")
            return cache['token']
        else:
            print("⏰ Cached token expired, requesting new one...")
            os.remove(cache_file)
            return None
    except (json.JSONDecodeError, KeyError, ValueError):
        print("⚠️ Invalid cache file, requesting new token...")
        if os.path.exists(cache_file):
            os.remove(cache_file)
        return None


def save_token_cache(token):
    """Save JWT token to cache."""
    cache_file = "/tmp/maestro_jwt_cache.json"
    # Assume token expires in 30 minutes (Census default)
    expires_at = datetime.now() + timedelta(minutes=30)
    
    cache = {
        'token': token,
        'expires_at': expires_at.isoformat(),
        'created_at': datetime.now().isoformat()
    }
    
    with open(cache_file, 'w') as f:
        json.dump(cache, f)
    
    print(f"💾 Token cached until {expires_at.strftime('%H:%M:%S')}")


async def get_auth_token():
    """Get auth token via OTP flow or from cache."""
    # Check cache first
    cached_token = load_cached_token()
    if cached_token:
        return cached_token
    
    email = "kjeld.oostra@pulsr.io"
    census_url = "http://localhost:8002"
    
    async with httpx.AsyncClient() as client:
        # Step 1: Request OTP
        print(f"🔐 Requesting OTP for {email}...")
        response = await client.post(f"{census_url}/api/v1/auth/login", json={"email": email})
        
        if response.status_code != 200:
            print(f"❌ Login failed: {response.status_code} - {response.text}")
            return None
        
        session_id = response.json()["session_id"]
        print(f"✅ OTP sent! Session ID: {session_id}")
        
        # Step 2: Get OTP
        otp_code = input("📱 Enter OTP from email: ").strip()
        
        # Step 3: Verify OTP
        print("🔓 Verifying OTP...")
        response = await client.post(f"{census_url}/api/v1/auth/verify-otp", json={
            "session_id": session_id,
            "otp_code": otp_code
        })
        
        if response.status_code != 200:
            print(f"❌ OTP verification failed: {response.status_code} - {response.text}")
            return None
        
        token = response.json()["access_token"]
        print(f"✅ Got auth token: {token[:20]}...")
        
        # Cache the token
        save_token_cache(token)
        return token


async def get_user_info(token):
    """Get user info from Census using the token."""
    census_url = "http://localhost:8002"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{census_url}/api/v1/users/me", headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Failed to get user info: {response.status_code} - {response.text}")
            return None


async def get_user_subtenant_access(token, user_id):
    """Get user's current subtenant access from Census."""
    census_url = "http://localhost:8002"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{census_url}/api/v1/users/{user_id}/service-access",
            headers=headers,
            params={"active_only": True}
        )
        if response.status_code == 200:
            service_access_list = response.json()
            # Find active access for maestro service
            for access in service_access_list:
                if access.get("service") == "maestro" and access.get("active"):
                    return access.get("subtenant_id")
            return None
        else:
            print(f"❌ Failed to get subtenant access: {response.status_code} - {response.text}")
            return None


async def grant_subtenant_access(token, user_id, subtenant_id):
    """Grant user access to subtenant via Census."""
    census_url = "http://localhost:8002"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{census_url}/api/v1/users/{user_id}/service-access",
            headers=headers,
            json={
                "service": "maestro",
                "subtenant_id": subtenant_id
            }
        )
        if response.status_code == 200:
            print("✅ Subtenant access granted in Census")
            return True
        else:
            print(f"❌ Failed to grant subtenant access: {response.status_code} - {response.text}")
            return False


async def test_maestro_with_auth(token):
    """Test Maestro functionality with auth token."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    maestro_url = "http://localhost:8800"
    
    # Get user info first
    user_info = await get_user_info(token)
    if not user_info:
        print("❌ Cannot get user info, aborting test")
        return
    
    user_id = user_info["id"]
    print(f"👤 User ID: {user_id}")
    
    # Check if user already has subtenant access
    print("\n🔍 Checking existing subtenant access...")
    existing_subtenant = await get_user_subtenant_access(token, user_id)
    
    if existing_subtenant:
        print(f"✅ User already has access to subtenant: {existing_subtenant}")
        subtenant_id = existing_subtenant
    else:
        print("➕ No existing subtenant found, creating new one...")
        
        async with httpx.AsyncClient() as client:
            print("\n📋 Creating subtenant...")
            response = await client.post(f"{maestro_url}/api/v1/subtenants", 
                headers=headers,
                json={"description": "Test subtenant"}
            )
            if response.status_code == 200:
                subtenant_data = response.json()
                subtenant_id = str(subtenant_data["id"])
                print(f"✅ Subtenant created in Maestro with ID: {subtenant_id}")
            else:
                print(f"❌ Subtenant creation failed: {response.status_code} - {response.text}")
                return
            
            # Grant user access to subtenant via Census
            print("\n🔗 Linking user to subtenant in Census...")
            if not await grant_subtenant_access(token, user_id, subtenant_id):
                print("⚠️ Could not grant subtenant access, continuing anyway...")
                return
            
            # Wait a moment for the relationship to propagate
            await asyncio.sleep(2)
    
    print(f"\n🎯 Using subtenant: {subtenant_id}")
    
    async with httpx.AsyncClient() as client:
        
        print("\n⚙️ Creating function...")
        response = await client.post(f"{maestro_url}/api/v1/functions",
            headers=headers,
            json={
                "name": "hello_test",
                "description": "Test function", 
                "code": "def hello_test(input):\n    return f\"Hello {input.get('name', 'World')}!\"",
                "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
                "output_schema": {"type": "string"}
            }
        )
        if response.status_code == 200:
            print("✅ Function created")
        else:
            print(f"❌ Function creation failed: {response.status_code} - {response.text}")
            return
        
        print("\n🔗 Creating webhook...")
        webhook_data = {
            "path": "test-function",  # No leading slash
            "function_name": "hello_test",
            "description": "Test webhook for hello_test function",
            "is_active": True
        }
        response = await client.post(f"{maestro_url}/api/v1/webhooks", 
            headers=headers, 
            json=webhook_data
        )
        if response.status_code == 200:
            webhook_id = response.json()["id"]
            print(f"✅ Webhook created: {webhook_id}")
        else:
            print(f"❌ Webhook creation failed: {response.status_code} - {response.text}")
            return
        
        print("\n🚀 Triggering function via webhook...")
        response = await client.post(f"{maestro_url}/api/v1/h/test-function",
            headers=headers,
            json={"name": "Test User"}
        )
        if response.status_code == 200:
            execution_data = response.json()
            execution_id = execution_data.get("execution_id")
            print(f"✅ Function triggered: {execution_id}")
            
            if execution_id:
                # Wait and check result
                await asyncio.sleep(2)
                response = await client.get(f"{maestro_url}/api/v1/executions/{execution_id}", headers=headers)
                if response.status_code == 200:
                    result = response.json()
                    print(f"✅ Result: {result.get('output_data')}")
                else:
                    print(f"❌ Could not get result: {response.status_code} - {response.text}")
            else:
                print("⚠️ No execution ID returned")
        else:
            print(f"❌ Webhook trigger failed: {response.status_code} - {response.text}")
        
        print("\n🧹 Cleaning up...")
        # Clean up webhook
        try:
            response = await client.delete(f"{maestro_url}/api/v1/webhooks/{webhook_id}", headers=headers)
            if response.status_code == 200:
                print("✅ Webhook cleaned up")
            else:
                print(f"⚠️ Webhook cleanup failed: {response.status_code}")
        except:
            print("⚠️ Webhook cleanup failed")
        
        # Clean up function (this will also clean up versions)
        try:
            response = await client.delete(f"{maestro_url}/api/v1/functions/hello_test", headers=headers)
            if response.status_code == 200:
                print("✅ Function cleaned up")
            else:
                print(f"⚠️ Function cleanup failed: {response.status_code}")
        except:
            print("⚠️ Function cleanup failed")
        
        # Only cleanup subtenant if we created a new one (not if we reused existing)
        if not existing_subtenant:
            response = await client.delete(f"{maestro_url}/api/v1/subtenants/{subtenant_id}", headers=headers)
            if response.status_code == 200:
                print("✅ Subtenant cleanup complete")
            else:
                print(f"⚠️ Subtenant cleanup failed: {response.status_code}")
        else:
            print("💡 Skipped subtenant cleanup (using existing subtenant)")


async def main():
    print("🚀 Simple Maestro Integration Test")
    print("=" * 40)
    
    # Check for --refresh flag
    if "--refresh" in sys.argv:
        cache_file = "/tmp/maestro_jwt_cache.json"
        if os.path.exists(cache_file):
            os.remove(cache_file)
            print("🗑️ Cleared token cache")
    
    # Get auth token
    token = await get_auth_token()
    if not token:
        print("❌ Authentication failed")
        sys.exit(1)
    
    # Test Maestro
    await test_maestro_with_auth(token)
    print("\n🎉 Test complete!")
    print("\n💡 Tip: Use --refresh to force new OTP if token is cached")


if __name__ == "__main__":
    asyncio.run(main())