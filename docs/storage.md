# Workspaces, project files, and personal reference

The installed app is replaceable. A workspace is your durable design data. Runtime
logs, process identity, selection, traces, and CAD caches live separately.

## Two supported layouts

Project-local:

```text
my-project/
  .voicedesign.toml       # workspace = "cad"
  cad/
    designs/<design-id>/
      model.py
      design.json
      history/*.json
    exports/
  other project files...
```

Central library:

```text
CADLibrary/
  sensor-case/designs/...
  desk-organizer/designs/...
```

Each external project keeps a `.voicedesign.toml` pointing to its own library
workspace. The library contains CAD data, not a copy of the software project.
Each workspace can hold multiple related parts.

Project-local storage travels with the repository and permits versioning CAD
source beside the project. Exports and large history folders need an intentional
Git policy. Central storage simplifies backups, but absolute pointers are
machine-specific and need adjustment when moving computers. Relative project
pointers are portable. Do not use synced/network folders for concurrent writers
on different computers: this is a local workstation app, not distributed storage.

## Resolution order

1. `voicedesign --workspace PATH ...` (relative to the calling directory).
2. Nearest `.voicedesign.toml` in the current directory or its ancestors. Its
   `workspace` path is relative to that configuration file.
3. `VOICEDESIGN_WORKSPACE`.
4. `default_workspace` in the per-user `config.toml`.
5. `<app-home>/workspaces/default`.

A project pointer intentionally takes precedence over the environment/default.
`voicedesign doctor` prints the resolved paths without starting CAD.

`VOICEDESIGN_HOME` overrides the app home. Defaults:

- Windows: `%LOCALAPPDATA%/VoiceDesign3D`
- Linux: `$XDG_DATA_HOME/VoiceDesign3D`, or `~/.local/share/VoiceDesign3D`
- macOS: `~/Library/Application Support/VoiceDesign3D` (not yet release-tested)

Example user configuration (TOML literal strings avoid Windows backslash escapes):

```toml
default_workspace = 'D:\CADLibrary\default'
```

The app home contains `runtime/<workspace-path-hash>/` for logs, lifecycle records,
and disposable caches. A small OS-held `.voicedesign.lock` prevents competing
managed servers or offline commands from writing the same workspace. Do not
delete locks to bypass running processes. Stop the workspace before moving it,
copying a consistent backup, or upgrading. Runtime files belong to one machine;
do not share `VOICEDESIGN_HOME` between computers/users.

CLI exports default to `./exports/<design-id>/` in the calling directory; use
`--output` for a project deliverable location. Offline exports default to the
workspace's `exports/`. Browser downloads follow browser settings.

## Printer information belongs in personal reference

Keep printer ownership/model, installed nozzle, available materials, slicer
profiles, measured fit clearances, and preferred settings in a personal reference
directory outside the app and published skill. For example, a personal
`3DModeling/README.md` can point to `printers.md` and current local defaults.
`PERSISTENT_AGENT_REFERENCE` can identify that directory in an agent environment.
The app does not automatically ingest or enforce it.

An app skill explains how to operate VoiceDesign3D. A broader design skill can
explain design and print preparation, consult personal reference, then invoke the
app skill. Neither should hardcode one user's printer as a universal requirement.
Copy relevant assumptions into a design's `print_notes` when they materially
affect the part, so its rationale survives changes to your printer setup. Keep
project requirements with the project.

## Existing checkout designs

Existing `designs/<id>/` directories are portable. With the old app stopped, copy
the complete tree, including `history/`, into the workspace's `designs/`. Compare
file hashes and build representative models before retiring the original.
`scripts/migrate_workspace.py` performs a checked, non-overwriting copy and writes
a manifest. No automatic migration deletes or rewrites saved designs. Do not copy
`.env`, virtual environments, node_modules, or CAD caches into the workspace.
