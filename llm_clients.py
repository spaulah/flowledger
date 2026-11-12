"""Build the chat and embedding clients from environment settings.

Two providers are supported:
  - "azure":  Azure OpenAI / Azure AI Foundry deployments.
  - "openai": any OpenAI-compatible API (OpenAI, Ollama, LM Studio, vLLM, ...).

Settings (see .env.example):
  LLM_PROVIDER           azure | openai. If unset, Azure is detected from the host name.
  LLM_BASE_URL           endpoint URL
  LLM_API_KEY            API key (optional for local servers)
  LLM_MODEL              chat model, or the deployment name on Azure
  EMBEDDING_MODEL        embedding model, or the deployment name on Azure
  LLM_AZURE_API_VERSION  Azure API version (default 2024-02-01)

The older names AZURE_AI_INFERENCE_ENDPOINT, AZURE_AI_INFERENCE_KEY,
AZURE_AI_INFERENCE_MODEL and AZURE_AI_EMBEDDINGS_MODEL are still read as fallbacks.
"""
import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings, ChatOpenAI, OpenAIEmbeddings

load_dotenv()

logger = logging.getLogger(__name__)

CHAT_TEMPERATURE = 0.3
DEFAULT_CHAT_MODEL = "gpt-4o"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_AZURE_API_VERSION = "2024-02-01"
# Used when a local server needs no key, since the OpenAI client requires a non-empty value.
NO_KEY_PLACEHOLDER = "not-needed"

_warned_legacy = set()


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    base_url: str
    api_key: str
    chat_model: str
    embedding_model: str
    azure_api_version: str


def _setting(name, *legacy_names, default=None):
    value = os.getenv(name)
    if value:
        return value
    for legacy in legacy_names:
        value = os.getenv(legacy)
        if value:
            if legacy not in _warned_legacy:
                _warned_legacy.add(legacy)
                logger.warning(f"{legacy} is deprecated. Rename it to {name} in .env.")
            return value
    return default


def _is_azure_host(url):
    return "cognitiveservices.azure.com" in url or "openai.azure.com" in url


def _resolve_provider(base_url):
    explicit = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit:
        if explicit not in ("azure", "openai"):
            raise ValueError(f"LLM_PROVIDER must be 'azure' or 'openai', not {explicit!r}.")
        return explicit
    return "azure" if _is_azure_host(base_url) else "openai"


def _openai_base_url(url):
    url = url.rstrip("/")
    return url if url.endswith("/v1") else f"{url}/v1"


def load_settings():
    """Read and validate the LLM settings from the environment."""
    base_url = _setting("LLM_BASE_URL", "AZURE_AI_INFERENCE_ENDPOINT")
    if not base_url:
        raise ValueError("LLM_BASE_URL is not set. Copy .env.example to .env and fill it in.")

    provider = _resolve_provider(base_url)
    api_key = _setting("LLM_API_KEY", "AZURE_AI_INFERENCE_KEY")
    if provider == "azure" and not api_key:
        raise ValueError("LLM_API_KEY is not set (required for Azure).")

    return LLMSettings(
        provider=provider,
        base_url=base_url,
        api_key=api_key or NO_KEY_PLACEHOLDER,
        chat_model=_setting("LLM_MODEL", "AZURE_AI_INFERENCE_MODEL", default=DEFAULT_CHAT_MODEL),
        embedding_model=_setting("EMBEDDING_MODEL", "AZURE_AI_EMBEDDINGS_MODEL",
                                 default=DEFAULT_EMBEDDING_MODEL),
        azure_api_version=_setting("LLM_AZURE_API_VERSION", default=DEFAULT_AZURE_API_VERSION),
    )


def build_chat_model(settings=None):
    s = settings or load_settings()
    if s.provider == "azure":
        return AzureChatOpenAI(
            azure_endpoint=s.base_url,
            api_key=s.api_key,
            azure_deployment=s.chat_model,
            api_version=s.azure_api_version,
            temperature=CHAT_TEMPERATURE,
        )
    return ChatOpenAI(
        api_key=s.api_key,
        base_url=_openai_base_url(s.base_url),
        model=s.chat_model,
        temperature=CHAT_TEMPERATURE,
    )


def build_embeddings(settings=None):
    s = settings or load_settings()
    if s.provider == "azure":
        return AzureOpenAIEmbeddings(
            azure_endpoint=s.base_url,
            api_key=s.api_key,
            azure_deployment=s.embedding_model,
            api_version=s.azure_api_version,
        )
    return OpenAIEmbeddings(
        api_key=s.api_key,
        base_url=_openai_base_url(s.base_url),
        model=s.embedding_model,
    )
