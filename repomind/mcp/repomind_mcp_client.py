from langchain_mcp_adapters.client import MultiServerMCPClient
from repomind.mcp.repomind_mcp_config import load_repomind_mcp_configs
from repomind.observability.logger import get_logger


logger = get_logger(__name__)


async def get_repomind_mcp_tools(overrides: dict | None = None, only: set | None = None,
                                 skip: set | None = None) -> list:
  """Connect to all configured MCP servers and return their tools.

  Servers are connected one at a time so a single broken server (e.g. npx
  missing, no network) is skipped instead of stopping RepoMind from starting.
  """
  configs = load_repomind_mcp_configs(overrides, only, skip)
  logger.info(f"Connecting to MCP servers: {list(configs.keys())}")
  tools = []
  for name, server_config in configs.items():
      try:
          client = MultiServerMCPClient({name: server_config})
          tools.extend(await client.get_tools())
      except Exception as e:
          logger.warning(f"Skipping MCP server '{name}': {e}")
  logger.info(f"Loaded {len(tools)} tools from MCP servers")
  return tools
