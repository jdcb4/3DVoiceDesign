# Agent integration

The repository includes a **draft** portable app skill at
`integrations/codex/voicedesign3d/SKILL.md`. Release installation does not copy it
into a personal skill directory. Its final scope and installation are separate
user decisions. `Install-CodexSkill.ps1` remains an explicit opt-in helper.

The app provides a CLI and HTTP interface; there is no MCP server. Agents can use
`voicedesign doctor`, `start`, and `api` from any configured project. The skill
teaches workspace targeting, revision-safe edits, focused discovery, and recovery.
See [API contract](api.md) and [storage](storage.md).

A practical split is:

- General 3D-design skill: design intent, manufacturing constraints, print preparation.
- App skill: operate VoiceDesign3D and verify its results.
- Personal persistent reference: current printer, nozzle, materials, calibrated fits,
  local library location, and preferences.
- Project/workspace: project requirements, editable parts, histories, and print notes.

A general design skill can invoke the app skill when this tool is appropriate.
The app remains useful to agents without either skill via its capability/schema
endpoints. Personal facts are not compiled into the app or published skill.

The viewer's AI connection card identifies an exact design and its workspace.
Use it when the agent does not share the current project's configuration. Honor
pending human edits and retain explicit design IDs across a conversation.
