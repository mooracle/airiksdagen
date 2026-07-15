# Deploying airiksdagen.se to Cloudflare Pages

The site is a static Astro build served from **Cloudflare Pages** using **Git
integration**: Cloudflare clones `mooracle/aidag`, runs the build itself on every
push to `main`, and publishes `site/dist`. There is no GitHub Actions deploy — the
old one was removed. (`.github/workflows/ci.yml` still runs tests only.)

## Pieces

| File | Role |
|------|------|
| `scripts/cf_pages_build.sh` | The build: install uv → rebuild data → `export-site` → `astro build` |
| `.node-version` | Pins Node 22 for Cloudflare's build image |
| `wrangler.toml` | Project name (`airiksdagen`) + output dir (`site/dist`) |
| `site/public/_headers` | Security headers + long cache on `/_astro/*` |

`RUN_ID` selects the published simulation run (defaults to `full-v3` in the build
script). To publish a different run, set `RUN_ID` as a build environment variable
in the Cloudflare dashboard.

## One-time setup (Cloudflare dashboard)

### 1. Give Cloudflare access to the repo
- **Workers & Pages → Create → Pages → Connect to Git**.
- **Connect GitHub**, authorize the **Cloudflare Pages** GitHub App for the
  **mooracle** org, grant it the **aidag** repo. Because it's an org private repo,
  an org owner may need to approve the app under
  `github.com/organizations/mooracle/settings/installations`.
- Select `mooracle/aidag`, production branch **`main`**.

### 2. Build configuration
- **Framework preset:** None (the script does everything).
- **Build command:** `bash scripts/cf_pages_build.sh`
- **Build output directory:** `site/dist` (also declared in `wrangler.toml`).
- **Root directory:** repo root (default).
- Project **name** must be **`airiksdagen`** to match `wrangler.toml`.

uv fetches Python 3.12 during the build; Node comes from `.node-version`. Nothing
else needs configuring — no API token or secrets (that was the old Actions path).

### 3. Custom domain airiksdagen.se
Pages project → **Custom domains → Set up a domain** → `airiksdagen.se`
(and optionally `www`).
- **If the zone is on Cloudflare** (nameservers point to Cloudflare): the record is
  created automatically, apex included.
- **If DNS is hosted elsewhere:** move the zone to Cloudflare for apex support, or
  point a `CNAME` at `airiksdagen.pages.dev` (apex needs CNAME flattening / ALIAS).
  The domain currently points at GitHub Pages — cut it over here.

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
npx wrangler pages deploy        # reads name + dir from wrangler.toml
```

## Notes
- The build rebuilds `data/processed/**` from public sources each run — gitignored,
  so nothing extra needs committing to publish.
- `site/src/data/` and `site/dist/` are gitignored (fully derived).
- The old GitHub Pages `CNAME` file was removed; Cloudflare sets the custom domain
  in the dashboard.
