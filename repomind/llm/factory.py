import os
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache

from langchain_core.rate_limiters import InMemoryRateLimiter

from repomind.config import config
from repomind.observability.logger import get_logger

logger = get_logger(__name__)

PROVIDERS = ("gemini", "openai", "anthropic")
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash", "openai": "gpt-5.5", "anthropic": "claude-sonnet-5-5"}


@dataclass(frozen=True)
class UserLLM:
  """A visitor's own provider + key (web UI "Connect your AI"). Held in memory per request, never stored."""
  provider: str
  api_key: str
  model: str


# Set by the web server for a request (and the background tasks it starts); None = the server's Gemini key.
_user_llm: ContextVar[UserLLM | None] = ContextVar("repomind_user_llm", default=None)


def use_user_llm(settings: UserLLM | None) -> None:
  _user_llm.set(settings)


def user_llm() -> UserLLM | None:
  return _user_llm.get()


def current_model_name() -> str:
  """The chat model a call made now would use (also keys the semantic cache)."""
  settings = _user_llm.get()
  if settings:
      return f"{settings.provider}:{settings.model}"
  return os.getenv("GEMINI_MODEL") or config["llm"]["model"]


def _gemini_key() -> str:
  key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
  if not key:
      raise RuntimeError(
          "GEMINI_API_KEY is not set. Add it to .env (get one at https://aistudio.google.com/apikey)."
      )
  return key


@lru_cache
def _rate_limiter() -> InMemoryRateLimiter | None:
  # One limiter for the whole process, so the agent, planner, judge and
  # summarizer share the same requests-per-minute budget (free tier is ~10 RPM).
  rpm = float(os.getenv("GEMINI_RPM") or config["llm"].get("requests_per_minute") or 0)
  if rpm <= 0:
      return None
  return InMemoryRateLimiter(requests_per_second=rpm / 60, check_every_n_seconds=0.1, max_bucket_size=1)


def build_chat_model(provider: str, model: str, api_key: str, max_tokens: int | None = None, rate_limiter=None):
  if provider == "gemini":
      from langchain_google_genai import ChatGoogleGenerativeAI
      # temperature is left at the model default: Gemini 3+ models are tuned for it.
      return ChatGoogleGenerativeAI(
          model=model, google_api_key=api_key, max_output_tokens=max_tokens,
          rate_limiter=rate_limiter, max_retries=6,  # backs off on 429 RESOURCE_EXHAUSTED
      )
  if provider == "openai":
      from langchain_openai import ChatOpenAI
      return ChatOpenAI(model=model, api_key=api_key, max_tokens=max_tokens, max_retries=3)
  if provider == "anthropic":
      from langchain_anthropic import ChatAnthropic
      return ChatAnthropic(model=model, api_key=api_key, max_tokens=max_tokens or 8192, max_retries=3)
  raise ValueError(f"Unknown provider: {provider} (use one of {', '.join(PROVIDERS)})")


def get_llm(model: str | None = None, max_tokens: int | None = None):
  """Return the chat model: the visitor's own (web UI) or the server's Gemini from config.

  model overrides the server's Gemini model (e.g. the judge model); a visitor's own
  connection always uses the model they chose.
  """
  settings = _user_llm.get()
  if settings:
      logger.info(f"Using visitor's LLM: {settings.provider}, model: {settings.model}")
      return build_chat_model(settings.provider, settings.model, settings.api_key, max_tokens)

  model = model or os.getenv("GEMINI_MODEL") or config["llm"]["model"]
  logger.info(f"Using LLM provider: gemini, model: {model}")
  return build_chat_model("gemini", model, _gemini_key(), max_tokens, rate_limiter=_rate_limiter())


def get_embedder():
  """Return the Gemini embedder. Indexer, retriever and cache must all use the same model.

  A visitor with their own Gemini key embeds with it; everyone else uses the server key
  (embeddings are always Gemini, so one index works whichever chat provider is used).
  """
  settings = _user_llm.get()
  key = settings.api_key if settings and settings.provider == "gemini" else _gemini_key()
  return _embedder(key, os.getenv("GEMINI_EMBEDDING_MODEL") or config["embeddings"]["model"], config["embeddings"]["dims"])


@lru_cache(maxsize=32)
def _embedder(api_key: str, model: str, dims: int):
  logger.info(f"Using embeddings provider: gemini, model: {model}, dims: {dims}")
  from langchain_google_genai import GoogleGenerativeAIEmbeddings
  return GoogleGenerativeAIEmbeddings(model=model, google_api_key=api_key, output_dimensionality=dims)
