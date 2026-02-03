"""Universal content chunk schemas - used internally after conversion."""
from typing import Literal, NotRequired, Union

from typing_extensions import TypedDict

# Provider-specific formats (internal use only, after conversion from universal schema)


class TextChunk(TypedDict):
    type: Literal["text"]
    text: str


class ImageURL(TypedDict):
    url: str
    detail: NotRequired[str]


class ImageURLChunk(TypedDict):
    type: Literal["image_url"]
    image_url: Union[ImageURL, str]


class InputAudioData(TypedDict):
    data: str
    format: str


class InputAudioChunk(TypedDict):
    type: Literal["input_audio"]
    input_audio: Union[InputAudioData, str]  # OpenAI uses object, Mistral uses string


class FileData(TypedDict):
    file_data: NotRequired[str]
    file_id: NotRequired[str]
    filename: NotRequired[str]


class FileChunk(TypedDict):
    type: Literal["file"]
    file: FileData


class DocumentURLChunk(TypedDict):
    type: Literal["document_url"]
    document_url: str
    document_name: NotRequired[str]


ContentChunk = Union[TextChunk, ImageURLChunk, InputAudioChunk, FileChunk, DocumentURLChunk]
