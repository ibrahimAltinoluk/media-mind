"""MCP sunucusu (stdio): Claude Desktop, Claude Code ve diğer MCP istemcileri için MediaMind araçları."""

from __future__ import annotations

import asyncio
import base64

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import __version__, tools
from . import settings as settings_mod
from .repo import Repo, RepoError

INSTRUCTIONS = (
    "MediaMind, kullanıcının yerel/harici disklerdeki fotoğraf ve video arşivlerini (repolar) yönetir. "
    "Önce list_repos ile repoları gör; birden çok repo varsa her çağrıda 'repo' ver. "
    "Dosyaları taşıma/silme işleri create_plan ile plan olarak hazırlanır; kullanıcı MediaMind uygulamasında onaylar. "
    "Arama tarih, yer, kamera, klasör, etiket ve dosya adıyla; içerik için search_media 'semantic', kişiler için list_people + 'person'."
)

_cache: dict[str, Repo] = {}


def _resolver(ref: str) -> Repo:
    repo = _cache.get(ref)
    if repo is None or not (repo.mm / "repo.toml").exists():
        try:
            repo = Repo.open(ref)
        except RepoError as e:
            raise tools.ToolError(str(e))
        repo.lock_timeout = 15.0
        _cache[ref] = repo
    return repo


def tool_list() -> list[types.Tool]:
    cfg = settings_mod.load()
    return [types.Tool(name=t["name"], description=t["description"], input_schema=t["schema"])
            for t in tools.TOOLS if t["name"] != "get_preview_image" or cfg["agents"]["send_previews"]]


def call(name: str, arguments: dict | None, client: str = "mcp") -> types.CallToolResult:
    ctx = tools.ToolContext(source=client, repo_resolver=_resolver, settings=settings_mod.load())
    try:
        res = tools.run(ctx, name, arguments or {})
    except tools.ToolError as e:
        return types.CallToolResult(content=[types.TextContent(type="text", text=str(e))], is_error=True)
    if "__image__" in res:
        return types.CallToolResult(content=[types.ImageContent(
            type="image", data=base64.b64encode(res["__image__"]).decode(), mime_type=res["media_type"])])
    return types.CallToolResult(content=[types.TextContent(type="text", text=tools.to_json(res))])


async def _on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=tool_list())


async def _on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    client = "mcp"
    try:
        info = ctx.session.client_params.client_info
        if info and info.name:
            client = f"mcp:{info.name}"
    except AttributeError:
        pass
    return await asyncio.to_thread(call, params.name, dict(params.arguments or {}), client)


def build_server() -> Server:
    return Server("mediamind", version=__version__, instructions=INSTRUCTIONS,
                  on_list_tools=_on_list_tools, on_call_tool=_on_call_tool)


def main() -> None:
    server = build_server()

    async def run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(run())
