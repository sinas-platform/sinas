"""Gemini LLM provider — OpenAI-compatible chat with Google-native batch files.

Chat completions, streaming, and tool calling run through Google's
OpenAI-compatible endpoint, inherited unchanged from OpenAIProvider.
Prompt caching is implicit on Gemini 2.5+ models (server-side, nothing to
send); cache hits surface via prompt_tokens_details.cached_tokens when the
compat layer reports them.

Batch is the hybrid flow from https://ai.google.dev/gemini-api/docs/openai#batch:
batches.create/retrieve/cancel work through the OpenAI client, but the
compat layer does NOT support files.create/files.content — the JSONL input
upload and result download must use Google's Files API, which the two hook
overrides below implement over plain REST.
"""
import logging
from typing import Any, Optional

import httpx

from .openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Google's SDK uploads batch JSONL with this literal mime type.
_JSONL_MIME = "jsonl"


class GeminiProvider(OpenAIProvider):
    supports_batch = True

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(api_key=api_key, base_url=base_url or DEFAULT_BASE_URL)

        # Derive the Files API roots from the compat base URL:
        #   https://host/v1beta/openai/  →  https://host/v1beta         (download)
        #                                →  https://host/upload/v1beta  (upload)
        root = (self.base_url or DEFAULT_BASE_URL).rstrip("/")
        if root.endswith("/openai"):
            root = root[: -len("/openai")]
        self._files_root = root
        scheme_host, _, api_path = root.partition("/v1")
        self._upload_root = f"{scheme_host}/upload/v1{api_path}"

    def _files_headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key or ""}

    async def _upload_batch_file(self, jsonl: bytes) -> str:
        """Upload via Google's resumable Files API; returns the file name
        (e.g. "files/abc123"), which the compat batches.create accepts as
        input_file_id."""
        async with httpx.AsyncClient(timeout=120.0) as client:
            start = await client.post(
                f"{self._upload_root}/files",
                headers={
                    **self._files_headers(),
                    "X-Goog-Upload-Protocol": "resumable",
                    "X-Goog-Upload-Command": "start",
                    "X-Goog-Upload-Header-Content-Length": str(len(jsonl)),
                    "X-Goog-Upload-Header-Content-Type": _JSONL_MIME,
                },
                json={"file": {"display_name": "sinas-batch.jsonl"}},
            )
            start.raise_for_status()
            upload_url = start.headers["X-Goog-Upload-URL"]

            finish = await client.post(
                upload_url,
                headers={
                    **self._files_headers(),
                    "X-Goog-Upload-Command": "upload, finalize",
                    "X-Goog-Upload-Offset": "0",
                },
                content=jsonl,
            )
            finish.raise_for_status()
            return finish.json()["file"]["name"]

    async def _download_batch_file(self, file_id: str) -> str:
        """Download a generated batch result file via the Files API."""
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            resp = await client.get(
                f"{self._files_root}/{file_id}:download",
                params={"alt": "media"},
                headers=self._files_headers(),
            )
            resp.raise_for_status()
            return resp.text
