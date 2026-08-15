// Module hooks so `node --test` resolves the site's imports the way Vite does.
//
// `src/` imports its own modules without an extension (`from './doctext'`), which
// is what every bundler expects and what plain Node refuses — it will not guess
// `.ts`. Astro never sees this file; it exists so the tests can import the real
// modules instead of a copy of them. Loaded through `node --import` by `npm test`.
import { registerHooks } from 'node:module';

registerHooks({
  resolve(specifier, context, next) {
    try {
      return next(specifier, context);
    } catch (err) {
      if (specifier.startsWith('.') && !/\.\w+$/.test(specifier)) {
        return next(`${specifier}.ts`, context);
      }
      throw err;
    }
  },
});
