# Deployment-specific Caddy routes

Drop your own Caddy route snippets here as `*.caddy` files. They are imported by
the main `Caddyfile` (`import conf.d/*.caddy`, inside the `:80 { ... }` site
block) and are **git-ignored**, so they survive every `git pull` / update — you
never have to edit the tracked `Caddyfile` and never hit a merge conflict on it.

## Example

Create `conf.d/mytool.caddy`:

```caddy
# Route an extra hostname/path to a custom service. handle blocks are matched
# most-specific-first, so this wins over the catch-all frontend route.
handle /mytool* {
	reverse_proxy my-tool-service:9000
}
```

Only put the **directives that belong inside the existing `:80 { ... }` site
block** here (e.g. `handle`, `route`, `reverse_proxy`, `redir`). Do not open a
new site block — these snippets are imported *inside* the existing one.

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
> directory do not have that problem.

## What is tracked

Only `.gitkeep` and this `README.md` are tracked. Your `*.caddy` files are
ignored (see the repo `.gitignore`).
