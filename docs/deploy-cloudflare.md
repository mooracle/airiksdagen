# Deploying airiksdagen.se to Cloudflare

The site is a static Astro build served from Cloudflare via **Git integration**:
Cloudflare clones `mooracle/aidag`, runs the build itself on every push to `main`,
and deploys `site/dist`. Cloudflare connected the repo as a **Workers Build**
(static assets) — the deploy step runs `npx wrangler deploy`, which uploads
`site/dist` as an assets-only Worker (no server code). There is no GitHub Actions
deploy — the old one was removed. (`.github/workflows/ci.yml` still runs tests only.)

## Pieces

| File | Role |
|------|------|
| `scripts/cf_pages_build.sh` | The build: install uv → rebuild data → `export-site` → `astro build` |
| `.node-version` | Pins Node 22 for Cloudflare's build image |
| `wrangler.toml` | Worker name (`airiksdagen`) + `[assets] directory = ./site/dist` |
| `site/public/_headers` | Security headers + long cache on `/_astro/*` (honored by Workers assets) |

`RUN_ID` selects the published simulation run (defaults to `full-v3` in the build
script). To publish a different run, set `RUN_ID` as a build environment variable
in the Cloudflare dashboard.

## One-time setup (Cloudflare dashboard)

### 1. Give Cloudflare access to the repo
- **Workers & Pages → Create → Import a repository** (Workers Build).
- **Connect GitHub**, authorize the **Cloudflare** GitHub App for the **mooracle**
  org, grant it the **aidag** repo. Because it's an org private repo, an org owner
  may need to approve the app under
  `github.com/organizations/mooracle/settings/installations`.
- Select `mooracle/aidag`, production branch **`main`**.

### 2. Build configuration
- **Build command:** leave **empty**. The build runs via `wrangler.toml`'s
  `[build] command = bash scripts/cf_pages_build.sh`, which `wrangler deploy`
  executes before uploading. (A dashboard build command would just double-run it.)
- **Deploy command:** `npx wrangler deploy` (the Workers Build default). It runs
  `[build]` → then uploads `[assets] directory = ./site/dist`.
- **Root directory:** repo root (default).
- Worker **name** must be **`airiksdagen`** to match `wrangler.toml`.

uv fetches Python 3.12 during the build; Node comes from `.node-version`. Nothing
else needs configuring — no API token or secrets (that was the old Actions path).

> **Why the build lives in `wrangler.toml` (not the dashboard):** a Workers Build
> only runs the *deploy* command by default, so if `site/dist` isn't built first
> you get *"assets.directory ... does not exist."* Putting the build in `[build]`
> makes `wrangler deploy` self-building and independent of dashboard fields.

> If you instead see a Pages-style project, the deploy runs `wrangler pages deploy`
> and expects `pages_build_output_dir` in `wrangler.toml`. This repo is set up for
> a **Workers Build** (`[assets]`), so keep the project a Workers Build — don't mix
> the two, or `wrangler deploy` errors with *"Missing entry-point / assets directory"*.

### 3. Custom domain airiksdagen.se
Worker → **Settings → Domains & Routes → Add → Custom domain** → `airiksdagen.se`
(and optionally `www`).
- **If the zone is on Cloudflare** (nameservers point to Cloudflare): the record is
  created automatically, apex included.
- **If DNS is hosted elsewhere:** move the zone to Cloudflare for apex support, or
  point a `CNAME` at `airiksdagen.<subdomain>.workers.dev` (apex needs CNAME
  flattening / ALIAS). The domain currently points at GitHub Pages — cut it over here.

## Publishing a new run
Bump `RUN_ID` (dashboard build env var) or the default in
`scripts/cf_pages_build.sh`, commit, push to `main`. Cloudflare rebuilds and
deploys. A push that only changes committed data under `data/results/**` also
triggers a rebuild.

## Deploying by hand (fallback, no Git build)
```bash
npx wrangler login
uv run aidag export-site --run-id full-v3
cd site && npx astro build && cd ..
npx wrangler deploy              # reads [assets] directory from wrangler.toml
# validate without deploying:  npx wrangler deploy --dry-run
```

## Notes
- The build rebuilds `data/processed/**` from public sources each run — gitignored,
  so nothing extra needs committing to publish.
- `site/src/data/` and `site/dist/` are gitignored (fully derived).
- The old GitHub Pages `CNAME` file was removed; Cloudflare sets the custom domain
  in the dashboard.
