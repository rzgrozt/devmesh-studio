# Migrating from the script-based DevMesh v0.x

DevMesh Studio intentionally uses a new local state model; it does not require the old bridge TOML, `.env`, or machine-token files.

1. Stop the old DevMesh gateway/bridge/tunnel so port 8000 is free, or choose a different port under **Connections**.
2. Launch `python3 devmesh.py` and create new OAuth credentials in the first-run dialog.
3. Add your codebases under **Repositories**. This replaces `config/bridge.toml` workspace entries.
4. Recreate any downstream MCP servers under **MCP Servers**. Only explicitly listed `allowed_tools` can be invoked remotely.
5. Review **Permissions** for each repository. Studio defaults to read/search allow, mutations ask, external paths and Git push deny.
6. Start the runtime in **Connections**, copy the newly generated `/mcp` URL, and replace the endpoint of your existing ChatGPT custom MCP app (or create a new one).
7. Complete OAuth again because DevMesh Studio uses a new signing secret and state database.

Your actual repositories are never migrated or copied; DevMesh Studio only stores their registered root paths.
