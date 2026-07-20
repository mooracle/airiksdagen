# Deploying airiksdagen.se to Cloudflare

Two clean halves:

- **Research (local, Python):** the `uv run aidag …` pipeline fetches votes, runs
  simulations, aggregates, and `export-site`. Its output — `site/src/data/` and
  `data/results/` — is **committed to the repo**.
- **Publish (cloud, Node only):** Cloudflare clones `mooracle/airiksdagen` and builds the
  Astro site straight from that committed data. **No Python, no uv, no data fetch**
  at build time — just `npm ci && astro build`. The only network the build touches
  is the npm registry.

Cloudflare connected the repo as a **Workers Build** (static assets): `wrangler
deploy` runs the `[build]` command, then uploads `dist` as an assets-only Worker
(no server code). There is no GitHub Actions deploy — the old one was removed.
(`.github/workflows/ci.yml` still runs tests only.)

**Cloudflare runs the build from the REPO ROOT** (its dependency step and
`npx wrangler deploy` both run at `/opt/buildhome/repo` — see the build logs). So
`wrangler.toml` and `.node-version` live at the repo root; `wrangler deploy` finds
`wrangler.toml`, runs `[build]` (`cd site && npm ci && npm run build`) to produce
`site/dist`, and uploads it. **Caveat:** because it runs at the root, Cloudflare's
dependency step detects the root `pyproject.toml`/`uv.lock` and runs `uv sync`
first — install only (~4s), it does NOT run the pipeline or fetch data. To
suppress it, set **Root directory = `site`** in the dashboard (scopes detection to
the Node-only `site/`); if you do, move `wrangler.toml`+`.node-version` back into
`site/` and drop the `cd site` / `./site/dist` prefixes.

## Pieces

| File | Role |
|------|------|
| `site/src/data/` | **Committed** site data (from `export-site`); Astro reads it directly |
| `.node-version` | Pins Node 22 for Cloudflare's build image |
| `wrangler.toml` | `[build]` = `cd site && npm ci && npm run build`; `[assets]` = `./site/dist` |
| `site/public/_headers` | Security headers + long cache on `/_astro/*` (honored by Workers assets) |

## One-time setup (Cloudflare dashboard)

### 1. Give Cloudflare access to the repo
- **Workers & Pages → Create → Import a repository** (Workers Build).
- **Connect GitHub**, authorize the **Cloudflare** GitHub App for the **mooracle**
  org, grant it the **aidag** repo. Because it's an org private repo, an org owner
  may need to approve the app under
  `github.com/organizations/mooracle/settings/installations`.
- Select `mooracle/airiksdagen`, production branch **`main`**.

### 2. Build configuration
- **Root directory:** leave as the repo root (default) — `wrangler.toml` is there.
- **Build command:** leave **empty**. The build runs via `wrangler.toml`'s
  `[build] command = cd site && npm ci && npm run build`, which `wrangler deploy`
  executes before uploading. (A dashboard build command would just double-run it.)
- **Deploy command:** `npx wrangler deploy` (the Workers Build default). It runs
  `[build]` → then uploads `[assets] directory = ./site/dist`.
- Worker **name** must be **`airiksdagen`** to match `wrangler.toml`.

Node comes from `.node-version`; no API token or secrets. (Cloudflare's own
`uv sync` step is separate — see the caveat above.)

> **Why the build lives in `wrangler.toml` (not the dashboard):** a Workers Build
> only runs the *deploy* command by default, so if `site/dist` isn't built first
> you get *"assets.directory ... does not exist."* Putting the build in `[build]`
> makes `wrangler deploy` self-building and independent of dashboard fields.

### 3. Custom domain airiksdagen.se
Worker → **Settings → Domains & Routes → Add → Custom domain** → `airiksdagen.se`
(and optionally `www`).
- **If the zone is on Cloudflare** (nameservers point to Cloudflare): the record is
  created automatically, apex included.
- **If DNS is hosted elsewhere:** move the zone to Cloudflare for apex support, or
  point a `CNAME` at `airiksdagen.<subdomain>.workers.dev` (apex needs CNAME
  flattening / ALIAS). The domain currently points at GitHub Pages — cut it over here.

## Publishing new data (the only step that touches Python — locally)
```bash
uv run aidag aggregate    --run-id full-v3
uv run aidag export-site  --run-id full-v3   # regenerates site/src/data/
git add site/src/data data/results/aggregates
git commit -m "publish: refresh site data"
git push                                     # Cloudflare rebuilds HTML only
```
To publish a different run, pass a different `--run-id` to `export-site` (and set
`site` in `astro.config.mjs` / the Worker as needed).

## Deploying by hand (fallback, no Git build)
```bash
npx wrangler login
cd site
npx wrangler deploy         # runs [build] (npm ci && npm run build), then uploads dist
# validate without deploying:  npx wrangler deploy --dry-run
```

## Notes
- `site/src/data/` **is committed** — the site builds from it with no fetch.
- `data/raw/`, `data/processed/`, `site/node_modules/`, `site/dist/` stay gitignored
  (raw/processed are local research intermediates, re-buildable from public sources).
- The old GitHub Pages `CNAME` file was removed; Cloudflare sets the custom domain
  in the dashboard.
