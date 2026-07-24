// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

import { localizedSlugs } from './src/i18n/ui.ts';

const SITE = (process.env.SITE_URL ?? 'https://airiksdagen.se').replace(/\/$/, '');

/** EN path (no /en prefix) -> canonical Swedish path. Inverse of localizedSlugs. */
function deLocalize(enPath) {
  for (const sv of ['/fall/', '/om/', '/metod/', '/dokument/', '/analys/', '/koalition/', '/parti/']) {
    const en = localizedSlugs('en', sv);
    if (enPath.startsWith(en)) return sv + enPath.slice(en.length);
  }
  return enPath;
}

/** Pair every URL with its counterpart. @astrojs/sitemap's own `i18n` option
 * keys locales off a path segment, so with prefixDefaultLocale:false it can
 * only ever match the Swedish root — hence building the alternates here. */
function alternates(url) {
  const p = new URL(url).pathname;
  const isEn = p === '/en' || p === '/en/' || p.startsWith('/en/');
  const svPath = isEn ? deLocalize(p.slice(3) || '/') : p;
  const enPath = `/en${localizedSlugs('en', svPath)}`;
  return [
    { lang: 'sv-SE', url: `${SITE}${svPath}` },
    { lang: 'en-US', url: `${SITE}${enPath}` },
  ];
}

export default defineConfig({
  site: process.env.SITE_URL ?? 'https://airiksdagen.se',
  base: process.env.SITE_BASE ?? '/',
  output: 'static',
  integrations: [
    sitemap({
      // /embed/* are noindex iframe widgets that duplicate the case pages.
      // Listing noindex URLs in a sitemap is a contradiction Search Console
      // reports as "Submitted URL marked noindex", so keep them out.
      filter: (page) => !page.includes('/embed/'),
      serialize: (item) => ({ ...item, links: alternates(item.url) }),
    }),
  ],
  i18n: {
    defaultLocale: 'sv',
    locales: ['sv', 'en'],
    routing: {
      prefixDefaultLocale: false,
    },
  },
  build: {
    concurrency: 4,
  },
});
