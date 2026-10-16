import os
import re
import json
from pathlib import Path
from dotenv import load_dotenv

from repomind.observability.logger import get_logger
from repomind.workspace import root

logger = get_logger(__name__)


load_dotenv()


_CONFIG_PATH = Path(__file__).parent.parent / "repomind_mcp_servers.json"


def load_repomind_mcp_configs(overrides: dict | None = None, only: set | None = None,
                              skip: set | None = None) -> dict:
 """Return mcp_servers dict from repomind_mcp_servers.json with env vars resolved.


 CWD is injected at load time so ${CWD} in the config always resolves to the
 project being worked on (launch directory, or the web workspace) — not the package install location.
 overrides replace env values (e.g. a web visitor's own GITHUB_TOKEN); only/skip pick servers by name.
 """
 values = {**os.environ, "CWD": str(root()), **(overrides or {})}
 servers = json.loads(_CONFIG_PATH.read_text()).get("mcp_servers", {})
 resolved = {}
 for name, server in servers.items():
     if (only is not None and name not in only) or (skip and name in skip):
         continue
     # Replace ${VAR} placeholders in the config with actual env var values
     text = json.dumps(server)
     missing = [var for var in re.findall(r"\$\{(\w+)\}", text) if not values.get(var)]
     if missing:
         logger.info(f"Skipping MCP server '{name}': {', '.join(missing)} not set")
         continue
     resolved[name] = json.loads(re.sub(r"\$\{(\w+)\}", lambda m: values[m.group(1)], text))
 return resolved
