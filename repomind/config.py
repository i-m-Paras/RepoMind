import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Loaded here, before anything reads the environment (env overrides below, API keys)
load_dotenv(Path(__file__).parent.parent / ".env")


def load_config() -> dict:
   """Load settings from config.yaml at the project root, then apply env overrides (for deployments)."""
   cfg = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text())
   if os.getenv("REDIS_URL"):
       cfg["semantic_cache"]["redis_url"] = os.environ["REDIS_URL"]
   if os.getenv("SEMANTIC_CACHE"):
       cfg["semantic_cache"]["enabled"] = os.environ["SEMANTIC_CACHE"].lower() in ("1", "true", "yes", "on")
   return cfg


config = load_config()
