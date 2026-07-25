import { execFileSync } from 'node:child_process';

/**
 * When the site was last actually updated — for sitemap <lastmod> and the
 * JSON-LD dates. Build-time only (Node); never reaches the client.
 *
 * Why one site-wide stamp rather than a date per page: `aidag export-site`
 * regenerates site/src/data/ wholesale, so a data run rewrites every case file
 * in a single commit (the last one touched all 2539 at once). Per-file git
 * dates would therefore collapse to the same value anyway, at the cost of
 * thousands of git calls per build.
 *
 * Why git rather than a `generated_at` field in the committed export: the
 * exporter deliberately pins gzip mtime=0 to keep its artifacts byte-identical
 * across re-runs, and a wall-clock stamp would put a spurious diff in every
 * export. The commit date is also the more honest answer — it is when the
 * published site changed, not when someone happened to re-run the exporter.
 *
 * Google only honours lastmod when it is "consistently and verifiably
 * accurate", so when the date cannot be established (git missing, or a shallow
 * clone whose one commit did not touch site/) this returns null and callers
 * omit the field entirely rather than guess. Set SITE_LASTMOD to override.
 */
let cached: string | null | undefined;

export function siteLastmod(): string | null {
  if (cached === undefined) cached = resolve();
  return cached;
}

function iso(value: string): string | null {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

function resolve(): string | null {
  const override = process.env.SITE_LASTMOD;
  if (override?.trim()) return iso(override.trim());
  try {
    // cwd is the Astro project root (site/) — Cloudflare runs `cd site && npm
    // run build`. The `.` pathspec therefore means "last commit touching site/",
    // which covers data, components, styles and copy: anything that changes the
    // rendered output. stderr is discarded so a non-repo build stays quiet.
    const out = execFileSync('git', ['log', '-1', '--format=%cI', '--', '.'], {
      cwd: process.cwd(),
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
    return out ? iso(out) : null;
  } catch {
    return null;
  }
}
