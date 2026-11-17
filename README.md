# OPSX

Reusable agent workflows, a project-local Git submodule, a Python CLI and a local
control panel. The first extracted release preserves the existing LangGraph
runtime and the remaining Cursor commands.

## Install the CLI

Python 3.12 or later is recommended for the current optional Cursor adapter.
The installer and web controller do not require model credentials.

    python -m pip install "git+https://github.com/revoluxe-ai/opsx.git@main"

For an editable core development checkout:

    python -m pip install -e ".[runtime,web,cursor,test]"

The repository is private. Git uses your normal GitHub authentication; OPSX does
not copy credentials into project configuration.

## Connect a project

Run at the root of an existing Git repository:

    opsx install --ref main
    opsx doctor
    opsx start

Installation adds the shared core at opsx/core, prepares an isolated execution
environment and installs OPSX Cursor commands. The canonical manually activated
@opsx context is versioned at `opsx/core/opsx/opsx.mdc` (`alwaysApply: false`).
Install seeds `opsx/opsx.mdc` from that pinned core only if absent; existing
consumer edits are Owner-owned and preserved through upgrades/uninstall.
An upgrade does not silently merge local context edits into the core.
AGENTS.md is not created or edited.

New project capabilities are deliberately unresolved. Use the installed
/opsx-adopt command to discover and configure the actual project. The installer
never copies stroy-system commands, database addresses or source roots into the
new project's settings.

For an intentional source-and-Cursor-only installation:

    opsx install --ref main --no-runtime

Doctor reports the omitted runtime as NOT_READY, rather than claiming that
workflow execution has been verified.

## Public commands

    opsx install --project .
    opsx upgrade --ref main
    opsx doctor
    opsx version
    opsx run CHANGE_ID
    opsx repair --bug "Describe one concrete defect"
    opsx start --port 8765
    opsx status
    opsx stop
    opsx uninstall

All commands accept --project and --json. The web panel listens on 127.0.0.1.
Repeated start returns the existing verified instance. Stop addresses only the
instance identified by that project's random token and identity.

## Ownership

| Location | Owner |
| --- | --- |
| opsx/core | Shared core Git repository |
| opsx/project | Project configuration, capabilities and infrastructure |
| opsx/install.json | Installed core pin and managed-file metadata |
| opsx/receipts | Per-project idempotency receipts |
| opsx/runs, opsx/logs | Per-project execution data |
| openspec | Product requirements and useful change artifacts |
| .cursor/commands | Managed OPSX files alongside unrelated project commands |
| opsx/opsx.mdc | Required manual OPSX engineering context (canonical) |

Generated Cursor files have one canonical source in the core. Local edits are
reported as conflicts during upgrade. Unrelated Cursor files are preserved.
Receipts and change artifacts are not reset by installation or upgrade.

Global CLI installation and the project's pinned core are separate. Workflow
workers execute from that project's checked-out core. Providers are selected by
the runtime's existing adapter registry.

## Upgrade and clone

    opsx stop
    opsx upgrade --ref main
    opsx doctor

Upgrade prepares the new environment and integration before publishing the new
installation metadata. A failed upgrade restores the previous core revision,
metadata and managed files. It does not commit or push the product repository.
Review and commit the Git submodule pin in your normal project workflow.

After cloning an already adopted project:

    git submodule update --init --recursive
    opsx install

The installer resolves the chosen branch or ref to an exact commit in each project.
Each project pins its own core commit; updating one consumer does not silently
update other projects. Core changes belong in this repository.

## Runtime and web architecture

The CLI and HTTP frontend share application services. Each workflow executes in
its own process with explicit project/core roots; installed core files are
resources, not product files. Persistent controller records retain exit codes.
The existing repair UI is preserved as an integration asset.

The web panel is the first management surface: project settings, execution
status, workflow launch and the repair entry. Runtime outcomes remain subject to
the actual verification gates. An exit from LangGraph is not a security PASS.

## Verification

    python -m unittest discover -s tests -v

Tests use real local Git repositories and submodules for installation, separate
projects, upgrades, conflict detection, rollback, configuration preservation,
uninstallation and the actual local HTTP server. They do not invoke a model.

SOURCE_EXPORT.json records the donor source revision. RELOCATION_REPORT.json
records the mechanical resource-path separation. This repository excludes the
donor's product source, product tests, changes, personal settings and run data.

Provider access and a live end-to-end change run are tested separately after project adoption. See [verification notes](VERIFICATION.md) for the exact checks and known inherited failures.
