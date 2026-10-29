"""Account-free stdio server for MCP integration checks."""
import asyncio
from mcp.server.fastmcp import FastMCP

server = FastMCP('veil-test')


@server.tool()
def echo(text: str) -> str:
    """Return the input unchanged."""
    return text


@server.tool()
async def slow() -> str:
    """Wait long enough to exercise a client timeout."""
    await asyncio.sleep(25)
    return 'done'


if __name__ == '__main__':
    server.run(transport='stdio')
