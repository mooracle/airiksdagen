import type { APIRoute } from 'astro';
import { getMemberRoster } from '../../../lib/data';

// The chamber's seat names, fetched once and shared by every case page and
// embed. Content-addressed: the filename is a hash of the list, because seats
// point into it by position. See getMemberRoster().
export function getStaticPaths() {
  return [{ params: { v: getMemberRoster().version } }];
}

export const GET: APIRoute = () =>
  new Response(JSON.stringify(getMemberRoster().list), {
    headers: { 'Content-Type': 'application/json' },
  });
