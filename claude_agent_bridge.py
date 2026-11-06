"""Ticket 228: the stdio MCP server Claude Code starts for the VEIL chat channel.

It holds no logic of its own. It lists the tools VEIL offers for this turn, forwards each call to the local
endpoint inside the VEIL process (claude_agent_channel.ToolServer), and answers Claude Code's permission
questions with the `approve` tool, which opens an approval card in Lan's chat.
"""
import asyncio
import json
import os
import urllib.request

URL = os.environ.get('VEIL_CC_URL', '')
TOKEN = os.environ.get('VEIL_CC_TOKEN', '')


def post(path, payload, timeout):
    request = urllib.request.Request(
        URL + path, data=json.dumps(payload).encode('utf-8'),
        headers={'X-Veil-Token': TOKEN, 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


APPROVE = {'name': 'approve', 'description': 'Ask Lan whether Claude Code may use a tool. Internal use only.',
           'inputSchema': {'type': 'object', 'properties': {
               'tool_name': {'type': 'string'}, 'input': {'type': 'object'}, 'tool_use_id': {'type': 'string'}},
               'required': ['tool_name', 'input']}}


async def serve():
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    server = Server('veil')

    @server.list_tools()
    async def list_tools():
        listed = await asyncio.to_thread(post, '/list', {}, 30)
        tools = [types.Tool(name=t['name'], description=t['description'], inputSchema=t['inputSchema'])
                 for t in listed.get('tools', []) if t['name'] != 'approve']
        tools.append(types.Tool(**APPROVE))
        return tools

    @server.call_tool()
    async def call_tool(name, arguments):
        if name == 'approve':
            # Waits for Lan's answer; no timeout here, the program side decides when to give up.
            answer = await asyncio.to_thread(post, '/approve', arguments, 24 * 3600)
            return [types.TextContent(type='text', text=json.dumps(answer))]  # one text block: the format Claude Code requires
        answer = await asyncio.to_thread(post, '/call', {'name': name, 'arguments': arguments}, 3600)
        if 'error' in answer:
            raise ValueError(answer['error'])
        return [types.TextContent(type='text', text=answer.get('text', ''))]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == '__main__':
    asyncio.run(serve())
