#!/usr/bin/env python3

import asyncio
import httpx
import json

# Example functions to demonstrate the platform


# Function definitions
EXAMPLE_FUNCTIONS = [
    {
        "name": "fetch_document",
        "description": "Fetch a document from a URL",
        "code": '''def fetch_document(input):
    import httpx
    
    url = input["url"]
    
    try:
        response = httpx.get(url, timeout=30)
        response.raise_for_status()
        
        return {
            "status": "success",
            "content": response.text,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", "unknown")
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }''',
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri"}
            },
            "required": ["url"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["success", "error"]},
                "content": {"type": "string"},
                "status_code": {"type": "integer"},
                "content_type": {"type": "string"},
                "error": {"type": "string"}
            },
            "required": ["status"]
        },
        "requirements": ["httpx"],
        "tags": ["utility", "web"]
    },
    {
        "name": "extract_invoice_data",
        "description": "Extract invoice data from document content",
        "code": '''def extract_invoice_data(input):
    import re
    
    content = input["content"]
    
    # Simple regex patterns for demo purposes
    invoice_number_match = re.search(r'Invoice(?:\s+#|\s+Number)?:?\s*([A-Z0-9-]+)', content, re.IGNORECASE)
    total_match = re.search(r'Total:?\s*\$?([0-9,]+\.?[0-9]*)', content, re.IGNORECASE)
    date_match = re.search(r'Date:?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})', content, re.IGNORECASE)
    
    return {
        "number": invoice_number_match.group(1) if invoice_number_match else "UNKNOWN",
        "total": float(total_match.group(1).replace(",", "")) if total_match else 0.0,
        "date": date_match.group(1) if date_match else None,
        "extracted": True
    }''',
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"}
            },
            "required": ["content"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "number": {"type": "string"},
                "total": {"type": "number"},
                "date": {"type": ["string", "null"]},
                "extracted": {"type": "boolean"}
            },
            "required": ["number", "total", "extracted"]
        },
        "requirements": [],
        "tags": ["data-extraction", "invoice"]
    },
    {
        "name": "validate_customer",
        "description": "Validate customer information against invoice data",
        "code": '''def validate_customer(input):
    customer_id = input["customer_id"]
    invoice_data = input["invoice_data"]
    
    # Simulate customer validation logic
    valid_customers = ["CUST001", "CUST002", "CUST003"]
    
    is_valid = customer_id in valid_customers
    
    if is_valid and invoice_data.get("total", 0) > 1000:
        risk_level = "high"
    elif is_valid:
        risk_level = "low"
    else:
        risk_level = "unknown"
    
    return {
        "customer_id": customer_id,
        "is_valid": is_valid,
        "risk_level": risk_level,
        "invoice_total": invoice_data.get("total", 0)
    }''',
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "invoice_data": {
                    "type": "object",
                    "properties": {
                        "total": {"type": "number"}
                    }
                }
            },
            "required": ["customer_id", "invoice_data"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "is_valid": {"type": "boolean"},
                "risk_level": {"type": "string", "enum": ["low", "high", "unknown"]},
                "invoice_total": {"type": "number"}
            },
            "required": ["customer_id", "is_valid", "risk_level", "invoice_total"]
        },
        "requirements": [],
        "tags": ["validation", "customer"]
    },
    {
        "name": "notify_team",
        "description": "Send notification to team",
        "code": '''def notify_team(input):
    import datetime
    
    message = input["message"]
    priority = input.get("priority", "normal")
    
    # Simulate notification (in real app, would send to Slack, email, etc.)
    notification = {
        "message": message,
        "priority": priority,
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "sent": True
    }
    
    return notification''',
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"}
            },
            "required": ["message"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "priority": {"type": "string"},
                "timestamp": {"type": "string"},
                "sent": {"type": "boolean"}
            },
            "required": ["message", "priority", "timestamp", "sent"]
        },
        "requirements": [],
        "tags": ["notification", "team"]
    },
    {
        "name": "process_invoice",
        "description": "Main invoice processing workflow",
        "code": '''def process_invoice(input):
    # This function orchestrates other functions
    doc = fetch_document({"url": input["document_url"]})
    
    if doc["status"] != "success":
        return {"status": "error", "error": "Failed to fetch document"}
    
    invoice_data = extract_invoice_data({"content": doc["content"]})
    
    validation = validate_customer({
        "customer_id": input["customer_id"],
        "invoice_data": invoice_data
    })
    
    # Send notification for high priority invoices
    if input.get("priority") == "high":
        notify_team({"message": f"High priority invoice: {invoice_data['number']}"})
    
    return {
        "invoice_number": invoice_data["number"],
        "total": invoice_data["total"],
        "customer_valid": validation["is_valid"],
        "risk_level": validation["risk_level"],
        "status": "processed"
    }''',
        "input_schema": {
            "type": "object",
            "properties": {
                "document_url": {"type": "string", "format": "uri"},
                "customer_id": {"type": "string"},
                "priority": {"type": "string", "enum": ["normal", "high"], "default": "normal"}
            },
            "required": ["document_url", "customer_id"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "invoice_number": {"type": "string"},
                "total": {"type": "number"},
                "customer_valid": {"type": "boolean"},
                "risk_level": {"type": "string"},
                "status": {"type": "string"}
            },
            "required": ["invoice_number", "total", "customer_valid", "risk_level", "status"]
        },
        "requirements": ["httpx"],
        "tags": ["workflow", "invoice", "main"]
    },
    {
        "name": "invoice_webhook_handler",
        "description": "Webhook handler for invoice processing",
        "code": '''def invoice_webhook_handler(input):
    # Extract data from webhook request
    document_url = input["body"].get("url")
    customer_id = input["headers"].get("X-Customer-ID", "unknown")
    priority = input["query"].get("priority", "normal")
    
    if not document_url:
        return {
            "success": False,
            "error": "Missing document URL in request body"
        }
    
    # Call the actual processing function
    result = process_invoice({
        "document_url": document_url,
        "customer_id": customer_id,
        "priority": priority
    })
    
    # Return webhook response
    return {
        "success": True,
        "invoice": result
    }''',
        "input_schema": {
            "type": "object",
            "properties": {
                "body": {"type": "object"},
                "headers": {"type": "object"},
                "query": {"type": "object"},
                "method": {"type": "string"},
                "url": {"type": "string"},
                "path": {"type": "string"}
            },
            "required": ["body"]
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "invoice": {"type": "object"},
                "error": {"type": "string"}
            },
            "required": ["success"]
        },
        "requirements": ["httpx"],
        "tags": ["webhook", "invoice", "handler"]
    }
]


async def create_example_functions():
    """Create example functions via API."""
    base_url = "http://localhost:8000/api/v1"
    
    async with httpx.AsyncClient() as client:
        for func_data in EXAMPLE_FUNCTIONS:
            try:
                response = await client.post(
                    f"{base_url}/functions",
                    json=func_data
                )
                
                if response.status_code == 200:
                    print(f"✓ Created function: {func_data['name']}")
                else:
                    print(f"✗ Failed to create function {func_data['name']}: {response.text}")
                    
            except Exception as e:
                print(f"✗ Error creating function {func_data['name']}: {e}")


async def create_example_webhook():
    """Create example webhook for invoice processing."""
    base_url = "http://localhost:8000/api/v1"
    
    webhook_data = {
        "path": "process-invoice",
        "function_name": "invoice_webhook_handler",
        "http_method": "POST",
        "description": "Process invoice documents",
        "default_values": {
            "priority": "normal"
        },
        "requires_auth": False
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{base_url}/webhooks",
                json=webhook_data
            )
            
            if response.status_code == 200:
                print(f"✓ Created webhook: {webhook_data['path']}")
                print(f"  URL: {base_url}/h/{webhook_data['path']}")
            else:
                print(f"✗ Failed to create webhook: {response.text}")
                
        except Exception as e:
            print(f"✗ Error creating webhook: {e}")


async def create_example_schedule():
    """Create example scheduled job."""
    base_url = "http://localhost:8000/api/v1"
    
    schedule_data = {
        "name": "daily-invoice-report",
        "function_name": "notify_team",
        "description": "Send daily invoice processing summary",
        "cron_expression": "0 9 * * *",  # 9 AM daily
        "timezone": "UTC",
        "input_data": {
            "message": "Daily invoice processing report generated",
            "priority": "normal"
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{base_url}/schedules",
                json=schedule_data
            )
            
            if response.status_code == 200:
                print(f"✓ Created scheduled job: {schedule_data['name']}")
            else:
                print(f"✗ Failed to create scheduled job: {response.text}")
                
        except Exception as e:
            print(f"✗ Error creating scheduled job: {e}")


async def main():
    print("Creating example functions, webhooks, and schedules...")
    print("Make sure the Maestro server is running on http://localhost:8000")
    print()
    
    await create_example_functions()
    print()
    
    await create_example_webhook()
    print()
    
    await create_example_schedule()
    print()
    
    print("Setup complete! Try these examples:")
    print("1. Test webhook: curl -X POST http://localhost:8000/api/v1/h/process-invoice \\")
    print("   -H 'Content-Type: application/json' \\")
    print("   -H 'X-Customer-ID: CUST001' \\")
    print("   -d '{\"url\": \"https://example.com/invoice.txt\"}'")
    print()
    print("2. Check executions: curl http://localhost:8000/api/v1/executions")
    print("3. View scheduler status: curl http://localhost:8000/scheduler/status")


if __name__ == "__main__":
    asyncio.run(main())