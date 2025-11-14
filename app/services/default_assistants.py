"""Initialize default assistants for common use cases."""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Assistant, User, Group
from app.core.config import settings
from app.core.auth import normalize_email

logger = logging.getLogger(__name__)


DEFAULT_TAGGER_CONFIG = {
    "name": "DEFAULT_TAGGER",
    "description": "Default assistant for extracting tags from documents and emails",
    "temperature": 0.1,
    "system_prompt": """You are a tag extraction assistant. You MUST respond ONLY with valid JSON. Do not include any explanatory text, greetings, or conversation.

## Tag Definitions to Extract:
{% for tag in tag_definitions %}
- **{{ tag.display_name }}** ({{ tag.name }})
  - Type: {{ tag.value_type }}
  {% if tag.description %}- Description: {{ tag.description }}{% endif %}
  {% if tag.allowed_values %}- Allowed values: {{ tag.allowed_values | join(', ') }}{% endif %}
  {% if tag.is_required %}- **REQUIRED**{% endif %}
{% endfor %}

## Instructions:
- Analyze the user's message (the content to tag)
- Extract values for each tag definition based on the content
- If a tag is **REQUIRED** but you cannot determine a value, use null
- If a tag is optional and not applicable, omit it from the response
- Be accurate and consistent
- Only extract information that is explicitly present or clearly implied in the content
- For multiple_choice tags, only use values from the allowed_values list
- For boolean tags, use "true" or "false" as strings
- For number tags, provide the numeric value as a string

## Output Format:
Respond ONLY with a JSON object in this exact format:
{
  "tags": [
    {"tag_name": "name_of_tag", "value": "extracted_value"},
    {"tag_name": "another_tag", "value": "another_value"}
  ]
}

Do NOT include any text before or after the JSON. Do NOT include markdown code blocks. ONLY output the raw JSON object.""",
    "input_schema": {
        "type": "object",
        "properties": {
            "tag_definitions": {
                "type": "array",
                "description": "List of tag definitions to extract",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "display_name": {"type": "string"},
                        "description": {"type": "string"},
                        "value_type": {
                            "type": "string",
                            "enum": ["free_text", "multiple_choice", "boolean", "number"]
                        },
                        "allowed_values": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "is_required": {"type": "boolean"}
                    },
                    "required": ["id", "name", "display_name", "value_type"]
                }
            }
        },
        "required": ["tag_definitions"]
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "description": "Extracted tags",
                "items": {
                    "type": "object",
                    "properties": {
                        "tag_name": {
                            "type": "string",
                            "description": "Name of the tag (e.g., 'year', 'quarter')"
                        },
                        "value": {
                            "type": ["string", "null"],
                            "description": "The extracted value (or null if required but not found)"
                        }
                    },
                    "required": ["tag_name", "value"]
                }
            }
        },
        "required": ["tags"]
    },
    "initial_messages": []
}


DEFAULT_SUMMARIZER_CONFIG = {
    "name": "DEFAULT_SUMMARIZER",
    "description": "Default assistant for generating summaries and descriptions",
    "temperature": 0.3,
    "system_prompt": """You are a document summarization assistant. Your job is to create clear, concise, and informative summaries of documents and emails.

## Task:
Generate a summary of the provided content.

{% if max_length %}**Maximum length:** {{ max_length }} sentences{% else %}**Target length:** 2-4 sentences{% endif %}
{% if focus %}**Focus area:** {{ focus }}{% endif %}

## Guidelines:
- Focus on main topics and key points
- Identify important entities (people, organizations, dates, amounts)
- Highlight action items or decisions (if applicable)
- Capture the overall purpose and context
- Be concise and informative
- Use clear, professional language""",
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The content to summarize"
            },
            "max_length": {
                "type": "integer",
                "description": "Maximum length in sentences (optional, default: 3)"
            },
            "focus": {
                "type": "string",
                "description": "Optional focus area (e.g., 'action items', 'technical details', 'financial information')"
            }
        },
        "required": ["content"]
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "The generated summary"
            },
            "key_entities": {
                "type": "array",
                "description": "Important entities mentioned (people, organizations, dates, amounts)",
                "items": {"type": "string"}
            }
        },
        "required": ["summary", "key_entities"]
    },
    "initial_messages": [
        {
            "role": "user",
            "content": """Content: "The board meeting on March 15, 2024 discussed Q1 performance. Revenue targets were met at $5.2M. Sarah Johnson proposed expanding the sales team by 3 positions. The proposal was approved unanimously. Implementation to begin in April."
"""
        },
        {
            "role": "assistant",
            "content": """{
  "summary": "Board meeting on March 15, 2024 reviewed successful Q1 performance with $5.2M revenue. Sarah Johnson's proposal to expand sales team by 3 positions was unanimously approved for April implementation.",
  "key_entities": ["Sarah Johnson", "March 15, 2024", "$5.2M", "Q1", "April"]
}"""
        }
    ]
}


async def initialize_default_assistants(db: AsyncSession):
    """
    Initialize DEFAULT_TAGGER and DEFAULT_SUMMARIZER assistants.

    These assistants are created by the admin user (from SUPERADMIN_EMAIL env var)
    and shared with the Users group for common use.
    """
    # Get admin user based on SUPERADMIN_EMAIL
    if not settings.superadmin_email:
        logger.warning("SUPERADMIN_EMAIL not set, skipping default assistant initialization")
        return

    email = normalize_email(settings.superadmin_email)

    # Get admin user
    result = await db.execute(
        select(User).where(User.email == email)
    )
    admin_user = result.scalar_one_or_none()

    if not admin_user:
        logger.warning(f"Admin user {email} not found, skipping default assistant initialization")
        return

    # Get Users group
    result = await db.execute(
        select(Group).where(Group.name == "Users")
    )
    users_group = result.scalar_one_or_none()

    if not users_group:
        logger.warning("Users group not found, skipping default assistant initialization")
        return

    for config in [DEFAULT_TAGGER_CONFIG, DEFAULT_SUMMARIZER_CONFIG]:
        # Check if assistant already exists
        result = await db.execute(
            select(Assistant).where(Assistant.name == config["name"])
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing assistant with latest config
            existing.description = config["description"]
            existing.temperature = config["temperature"]
            existing.system_prompt = config["system_prompt"]
            existing.input_schema = config["input_schema"]
            existing.output_schema = config["output_schema"]
            existing.initial_messages = config["initial_messages"]
            existing.user_id = admin_user.id
            existing.group_id = users_group.id

            await db.commit()
            logger.info(f"Updated default assistant: {config['name']}")
        else:
            # Create new assistant owned by admin, shared with Users group
            assistant = Assistant(
                user_id=admin_user.id,
                group_id=users_group.id,
                name=config["name"],
                description=config["description"],
                temperature=config["temperature"],
                system_prompt=config["system_prompt"],
                input_schema=config["input_schema"],
                output_schema=config["output_schema"],
                initial_messages=config["initial_messages"],
                is_active=True
            )
            db.add(assistant)
            await db.commit()
            logger.info(f"Created default assistant: {config['name']} (owner: {email}, group: Users)")
