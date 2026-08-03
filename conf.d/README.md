# Deployment-specific Caddy routes

Drop your own Caddy route snippets here as `*.caddy` files. They are imported by
the main `Caddyfile` and are **git-ignored**, so they survive every `git pull` /
update — you never have to edit the tracked `Caddyfile` and never hit a merge
conflict on it.

There are **two directories** because a Caddy snippet can be one of two shapes,
and each is only valid in one position:

| Put it in…       | For…                                   | The snippet contains…                     |
| ---------------- | -------------------------------------- | ----------------------------------------- |
| `conf.d/`        | an **extra hostname** (own site block) | a full `host:80 { … }` block              |
| `conf.d/site/`   | **extra paths** on the main `:80` host | directives only (`handle`, `route`, …)    |

The main `Caddyfile` imports `conf.d/*.caddy` at the **top level** (where whole
site blocks are legal) and `conf.d/site/*.caddy` **inside** the `:80 { … }` block
(where only directives are legal). Putting a file in the wrong directory produces
a Caddy parse error — a site block inside `:80 { … }`, or a bare `handle` at the
top level — so match the shape to the folder.

## Example — extra path on the main host → `conf.d/site/mytool.caddy`

```caddy
# Route an extra path to a custom service. handle blocks are matched
# most-specific-first, so this wins over the catch-all frontend route.
handle /mytool* {
	reverse_proxy my-tool-service:9000
}
```

Only **directives that belong inside the existing `:80 { … }` site block** go
here (e.g. `handle`, `route`, `reverse_proxy`, `redir`). Do **not** open a new
site block — these snippets are imported *inside* the existing one.

## Example — extra hostname → `conf.d/bridge.caddy`

Use this when a path set needs its **own hostname**, e.g. a Cloudflare-Access
*bypass* host: the Computer-Use bridge cannot send `CF-Access-Client-Id` /
`CF-Access-Client-Secret`, so its hostname must be set to *Bypass* in Cloudflare
Access, which in Caddy means a **separate site block**:

```caddy
# A dedicated bypass host: only the narrow set of paths the bridge needs is
# reachable here, without Access; everything else stays behind Access on the
# main host. Note: Caddy rejects an inline `handle /x { … }` one-liner
# ("Unexpected next token after '{' on same line") — put the block on its own
# lines as below.
bridge.example.com:80 {
	handle /ws/computer-use/bridge* {
		reverse_proxy ai-employee-orchestrator:8000
	}
	handle /api/v1/auth/login {
		reverse_proxy ai-employee-orchestrator:8000
	}
	handle /api/v1/computer-use* {
		reverse_proxy ai-employee-orchestrator:8000
	}
	handle /api/v1/agents* {
		reverse_proxy ai-employee-orchestrator:8000
	}
	handle {
		respond "Not found" 404
	}
}
```

Because this opens its own site block it must live in `conf.d/` (top level), not
in `conf.d/site/`.

## Applying changes

`conf.d/` is bind-mounted as a directory, so edits are visible to the container.
Reload with:

```sh
docker exec ai-employee-caddy caddy reload --config /etc/caddy/Caddyfile
```

> Note: the top-level `Caddyfile` itself is bind-mounted as a *single file*.
> Editing it with `sed -i` replaces the inode and Caddy keeps serving the stale
> config even though `caddy reload` reports success — use
> `docker restart ai-employee-caddy` after editing that file. Files in this
> directory (and `site/`) do not have that problem.

## What is tracked

Only `.gitkeep`, `site/.gitkeep` and this `README.md` are tracked. Your
`*.caddy` files (in both `conf.d/` and `conf.d/site/`) are ignored (see the repo
`.gitignore`).
