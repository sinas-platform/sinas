#!/usr/bin/env python3
"""
Debug script to check what webhooks exist in the database.
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
        
        # Check if token is expired
        expires_at = datetime.fromisoformat(cache['expires_at'])
        if datetime.now() + timedelta(minutes=5) < expires_at:
            return cache['token']
        else:
            return None
    except:
        return None


async def debug_webhooks():
    """Debug webhooks in the database."""
    token = load_cached_token()
    if not token:
        print("❌ No cached token found. Run the main test first.")
        return
    
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    maestro_url = "http://localhost:8800"
    
    async with httpx.AsyncClient() as client:
        print("🔍 Checking all webhooks...")
        response = await client.get(f"{maestro_url}/api/v1/webhooks", headers=headers)
        
        if response.status_code == 200:
            webhooks = response.json()
            print(f"✅ Found {len(webhooks)} webhooks:")
            for webhook in webhooks:
                print(f"  - ID: {webhook['id']}")
                print(f"    Path: '{webhook['path']}'")
                print(f"    Function: {webhook['function_name']}")
                print(f"    Method: {webhook['http_method']}")
                print(f"    Active: {webhook['is_active']}")
                print(f"    Subtenant: {webhook.get('subtenant_id', 'None')}")
                print()
        else:
            print(f"❌ Failed to get webhooks: {response.status_code} - {response.text}")
        
        # Try to manually test the webhook handler lookup logic
        print("🧪 Testing webhook handler lookup...")
        print("Looking for: path='test-function', method='POST', active=True")
        
        # Let's try triggering the webhook and see the debug output
        print("\n🚀 Triggering webhook to see debug output...")
        response = await client.post(f"{maestro_url}/api/v1/h/test-function",
            headers=headers,
            json={"name": "Debug Test"}
        )
        print(f"Response: {response.status_code} - {response.text}")


if __name__ == "__main__":
    asyncio.run(debug_webhooks())