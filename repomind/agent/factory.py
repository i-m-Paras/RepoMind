from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware

from repomind.llm.factory import get_llm
from repomind.agent.tools import search_codebase
from repomind.observability.logger import get_logger
from repomind.tools.terminal_tools import run_command, run_in_directory
from repomind.mcp.repomind_mcp_client import get_repomind_mcp_tools
from repomind.skills.skill_tools import load_skill, build_skills_prompt
from repomind.workspace import shell_allowed

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a senior software engineer with deep knowledge of the codebase.
Always use the search_codebase tool before answering any question.
Reference specific file names, function names and line numbers in your answers.
If you cannot find the answer in the codebase, say so explicitly."""

# Tools that only read; everything else (shell, file writes, GitHub changes) can need approval.
_READ_ONLY_TOOLS = {"search_codebase", "load_skill"}
_READ_ONLY_PREFIXES = ("read_", "list_", "get_", "search_", "directory_tree")


def _needs_approval(tool) -> bool:
   return tool.name not in _READ_ONLY_TOOLS and not tool.name.startswith(_READ_ONLY_PREFIXES)


async def build_agent(checkpointer, mcp_tools: list | None = None, require_approval: bool = False):
   """Create and return a LangChain agent with persistent memory.

   The web server passes its already-connected mcp_tools and require_approval=True,
   so file writes and commands pause for the user's approve / edit / reject.
   """
   llm = get_llm()
   if mcp_tools is None:
       mcp_tools = await get_repomind_mcp_tools()

   skills_prompt = build_skills_prompt()
   full_prompt = SYSTEM_PROMPT
   if skills_prompt:
       full_prompt = SYSTEM_PROMPT + "\n\n" + skills_prompt

   tools = [
       search_codebase,
       load_skill,
       *([run_command, run_in_directory] if shell_allowed() else []),
       *mcp_tools,
   ]

   middleware = []
   if require_approval:
       interrupt_on = {
           t.name: {"allowed_decisions": ["approve", "edit", "reject"]}
           for t in tools if _needs_approval(t)
       }
       middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))

   return create_agent(
       llm,
       tools=tools,
       system_prompt=full_prompt,
       checkpointer=checkpointer,
       middleware=middleware,
   )
