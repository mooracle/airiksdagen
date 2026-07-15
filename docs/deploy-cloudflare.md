# Deploying airiksdagen.se to Cloudflare

Two clean halves:

- **Research (local, Python):** the `uv run aidag …` pipeline fetches votes, runs
  simulations, aggregates, and `export-site`. Its output — `site/src/data/` and
  `data/results/` — is **committed to the repo**.
- **Publish (cloud, Node only):** Cloudflare clones `mooracle/aidag` and builds the
  Astro site straight from that committed data. **No Python, no uv, no data fetch**
  at build time — just `npm ci && astro build`. The only network the build touches
  is the npm registry.

Cloudflare connected the repo as a **Workers Build** (static assets): `wrangler
deploy` runs the `[build]` command, then uploads `dist` as an assets-only Worker
(no server code). There is no GitHub Actions deploy — the old one was removed.
(`.github/workflows/ci.yml` still runs tests only.)

**Cloudflare's Root directory is `site/` on purpose.** The repo root holds the
research pipeline's `pyproject.toml` + `uv.lock`; pointing Cloudflare at `site/`
keeps its build context Node-only, so it never detects or installs Python/uv.
`wrangler.toml` and `.node-version` live in `site/` for the same reason.

## Pieces

| File | Role |
|------|------|
| `site/src/data/` | **Committed** site data (from `export-site`); Astro reads it directly |
| `site/.node-version` | Pins Node 22 for Cloudflare's build image |
| `site/wrangler.toml` | `[build]` = `npm ci && npm run build`; `[assets]` = `./dist` |
| `site/public/_headers` | Security headers + long cache on `/_astro/*` (honored by Workers assets) |

## One-time setup (Cloudflare dashboard)

### 1. Give Cloudflare access to the repo
- **Workers & Pages → Create → Import a repository** (Workers Build).
- **Connect GitHub**, authorize the **Cloudflare** GitHub App for the **mooracle**
  org, grant it the **aidag** repo. Because it's an org private repo, an org owner
  may need to approve the app under
  `github.com/organizations/mooracle/settings/installations`.
- Select `mooracle/aidag`, production branch **`main`**.

### 2. Build configuration
- **Root directory:** **`site`** — REQUIRED. This is what keeps the build Node-only
  (no `pyproject.toml`/`uv.lock` in scope → Cloudflare never installs Python).
- **Build command:** leave **empty**. The build runs via `site/wrangler.toml`'s
  `[build] command = npm ci && npm run build`, which `wrangler deploy` executes
  before uploading. (A dashboard build command would just double-run it.)
- **Deploy command:** `npx wrangler deploy` (the Workers Build default). It runs
  `[build]` → then uploads `[assets] directory = ./dist` (i.e. `site/dist`).
- Worker **name** must be **`airiksdagen`** to match `wrangler.toml`.

Node comes from `site/.node-version`; no API token or secrets, no Python.

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
