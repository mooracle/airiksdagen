// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: process.env.SITE_URL ?? 'https://airiksdagen.se',
  base: process.env.SITE_BASE ?? '/',
  output: 'static',
  integrations: [sitemap()],
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
