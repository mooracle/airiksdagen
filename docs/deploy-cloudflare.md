# Deploying airiksdagen.se to Cloudflare Pages

The site is a static Astro build served from **Cloudflare Pages**. GitHub Actions
does the heavy build (Python pipeline + `astro build`) and hands the finished
`site/dist` to Cloudflare via `wrangler pages deploy`. This replaces the old
GitHub Pages deploy.

## Pieces

| File | Role |
|------|------|
| `.github/workflows/deploy.yml` | CI: rebuild data → `export-site` → `astro build` → `wrangler pages deploy` |
| `wrangler.toml` | Pages project name (`airiksdagen`) + output dir (`site/dist`) |
| `site/public/_headers` | Security headers + long cache on `/_astro/*` |

The `RUN_ID` env in `deploy.yml` selects the published simulation run
(`full-v3`). Change it when promoting a new run.

## One-time setup

### 1. Cloudflare API token + account ID
- Cloudflare dashboard → **My Profile → API Tokens → Create Token**.
- Use the **"Edit Cloudflare Pages"** template (scope: Account → *Cloudflare Pages* → *Edit*).
- Copy the token. Grab the **Account ID** from any domain's overview page (right sidebar).

### 2. GitHub repository secrets
In `mooracle/aidag` → **Settings → Secrets and variables → Actions → New repository secret**:
- `CLOUDFLARE_API_TOKEN` = the token above
- `CLOUDFLARE_ACCOUNT_ID` = the account ID

### 3. Create the Pages project
Either let the first deploy create it, or pre-create in the dashboard
(**Workers & Pages → Create → Pages → Direct upload**) with the name
**`airiksdagen`** and production branch **`main`**. The name must match
`wrangler.toml`.

### 4. Custom domain airiksdagen.se
In the Pages project → **Custom domains → Set up a domain** → `airiksdagen.se`
(and optionally `www.airiksdagen.se`).
- **If the zone `airiksdagen.se` is on Cloudflare** (nameservers point to Cloudflare):
  the DNS record is created automatically, apex included.
- **If DNS is hosted elsewhere:** move the zone to Cloudflare for apex support, or
  point a `CNAME` at `airiksdagen.pages.dev` (apex needs CNAME flattening / an
  ALIAS record). The domain currently points at GitHub Pages — cut it over here.

## How a deploy happens
Push to `main` touching `data/results/**`, `site/**`, `pipeline/**`,
`wrangler.toml`, or the workflow file → the Action runs and deploys. Or trigger
manually from the Actions tab (`workflow_dispatch`).

## Deploying by hand (local)
```bash
# one-time
npx wrangler login

# build the current published run, then deploy
uv run aidag export-site --run-id full-v3
cd site && npx astro build && cd ..
npx wrangler pages deploy        # reads name + dir from wrangler.toml
```

## Notes
- The Action rebuilds `data/processed/**` from public sources each run — it is
  gitignored, so nothing extra needs committing to publish.
- `site/src/data/` and `site/dist/` are gitignored (fully derived).
- The old `CNAME` file was removed; Cloudflare configures the custom domain in
  the dashboard, not via a repo file.
