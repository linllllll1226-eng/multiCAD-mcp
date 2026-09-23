# MCP compatibility

Version 0.5.0 supports Python 3.10–3.12 on Windows and the bounded stable
dependency line FastMCP >=4.0.5,<5 / MCP Python SDK >=2.2.0,<3.
uv.lock records the exact tested dependency set.

| Surface | Status and evidence |
|---|---|
| Local STDIO, initialize / 2025-11-25 | Supported; real subprocess integration tests |
| Local STDIO, discover / 2026-07-28 | Supported; real subprocess integration tests |
| tools/list, tools/call, text/structured results and tool errors | Tested on both protocol eras |
| Profile sync client | SDK v2 client; five profiles round-trip through isolated SQLite |
| HTTP MCP and dashboard | Experimental local single-process surface; unsupported for remote production |
| Codex / other interactive hosts | Configure local STDIO; a particular host version requires its own connection acceptance |

The HTTP server and CAD adapter context hold process-local state. Do not use
multiple workers, load balancing, remote exposure or production authentication
claims. Existing HTTP lifecycle tests do not establish remote deployment support.

Optional capabilities are not automatically project features merely because
the SDK supports them. This release does not implement project workflows for
Tasks, sampling, roots, elicitation, or authenticated remote access. Legacy UI
helpers are experimental and **not complete MCP Apps support**. No Apps
registration/postMessage end-to-end acceptance is claimed.

Migration references:
- [SDK v2 migration](https://py.sdk.modelcontextprotocol.io/migration/)
- [FastMCP 4 migration](https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3)

Use `src/server_memory.py` in a checkout or `multicad-mcp` from an installed wheel.
The guarded entry point exposes 25 tools and starts no HTTP listener. The local
development deployment can contain additional tools and is a separate source tree.
