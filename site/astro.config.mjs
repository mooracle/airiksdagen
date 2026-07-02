// @ts-check
import { defineConfig } from 'astro/config';

export default defineConfig({
  site: process.env.SITE_URL ?? 'https://aidag.example.org',
  base: process.env.SITE_BASE ?? '/',
  output: 'static',
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
