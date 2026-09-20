# people-finder client compatibility

people-finder is a client-neutral Agent Plugin. The canonical source of truth is
the portable package at the repository root (`plugin.json`, `mcp.json`,
`skills/people-finder/SKILL.md`, `./bin/people-finder`, `src/people_finder/`).
No client-specific packaging duplicates that source; adapters are pointers.

## Fixed component locations

Agent Plugins v1 defines two component types, both used here:

| Component | Location | Contents |
| --- | --- | --- |
| Skill | `skills/people-finder/SKILL.md` | workflow, tool names, argument shapes, boundaries |
| MCP server | `mcp.json` | one stdio server (`people-finder` -> `./bin/people-finder mcp`) |

Nothing is declared inline in `plugin.json`; the manifest carries metadata only.

## No data directory, on purpose

people-finder stores no state. It reads the files the caller names and writes
only the output path the caller names, so the MCP entry passes no data
directory and deliberately does **not** use `${PLUGIN_DATA}` or `${PLUGIN_ROOT}`
expansion in `args`, `env` or `cwd`. Declaring a store would claim a capability
the product does not have. The packaging check asserts both the absent
declaration and the untouched host-provided `PLUGIN_DATA` canary.

`env` is omitted entirely: the runtime needs no variable and inherits only the
client's baseline process environment.

## Matrix and probes

`compat/matrix.json` records the compatibility status of every intended client.
A status is `verified`, `built`, `intended`, or `unverified`, and only a status
proven by a real isolated launch claims `verified`.

Re-run the Hermes probe with:

    python3 tests/plugin/check_hermes_host_probe.py

The probe renders `compat/hermes/config.yaml.template` into a temporary
`HERMES_HOME`, registers nothing in the real user profile, runs the real host
surface (`hermes mcp test people-finder` and `hermes skills list`) there, and
reports the observed connection, discovered tool names and staged-skill
discovery. Real user profiles are never read or written.

## Thin adapter

`compat/hermes/config.yaml.template` is the only generated artifact:

```yaml
mcp_servers:
  people-finder:
    command: __PEOPLE_FINDER_LAUNCHER__
    args: [mcp]
```

`__PEOPLE_FINDER_LAUNCHER__` is replaced with the absolute path of the bundled
launcher at render time, because Hermes resolves `mcp_servers` commands in the
host environment rather than against a plugin root. The adapter contains no
discovery policy, no tool implementation, no backend choice and no credential.
Client-specific discovery behaviour would be added here only after a real
isolated launch proves it; unproven clients stay `unverified` and are never
claimed as supported.
