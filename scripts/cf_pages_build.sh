#!/usr/bin/env bash
# Cloudflare Pages (Git integration) build for airiksdagen.se.
#
# Set this as the project's *Build command* in the Cloudflare dashboard:
#     bash scripts/cf_pages_build.sh
# and *Build output directory* to  site/dist  (also declared in wrangler.toml).
#
# Cloudflare's build image ships Node (pinned by .node-version) but not uv, so
# we install uv here; uv fetches Python 3.12 itself if the image lacks it. The
# gitignored processed data is rebuilt from public sources on every build.
set -euo pipefail

RUN_ID="${RUN_ID:-full-v3}"

# uv is not preinstalled in the Pages build image.
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv sync

# Rebuild data/processed/** from public sources (gitignored, re-fetchable).
uv run aidag fetch-votes
uv run aidag fetch-cases
uv run aidag build-cases
uv run aidag verify votes
uv run aidag verify cases

uv run aidag export-site --run-id "$RUN_ID"

cd site
npm ci
npx astro build
