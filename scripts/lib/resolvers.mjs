/**
 * Runner-side resolvers for url tasks whose page cannot be fetched by the
 * Worker (site bans Cloudflare egress IPs). Mirrors src/resolvers/ in the
 * trove repo — keep the two in sync when a site's markup changes.
 *
 * resolveOnRunner(site, sourceUrl) -> { title, url, headers, durationSec? }
 */

const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

function parseHms(text) {
  const m = /(\d{1,2}):(\d{2}):(\d{2})/.exec(text || "");
  if (!m) return undefined;
  return Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]);
}

/**
 * 91porn embeds the direct link inside
 *   document.write(strencode2("<percent-encoded <source ...> tag>"))
 * where strencode2 is plain unescape. Verified 2026-11-02.
 */
async function resolve91porn(pageUrl) {
  const res = await fetch(pageUrl, {
    headers: {
      "User-Agent": UA,
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
      "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
      "Sec-Fetch-Dest": "document",
      "Sec-Fetch-Mode": "navigate",
      "Sec-Fetch-Site": "none",
      "Upgrade-Insecure-Requests": "1",
    },
    redirect: "follow",
  });
  if (!res.ok) throw new Error(`91porn returned HTTP ${res.status}`);
  const html = await res.text();

  const cipher = /strencode2\("([^"]+)"\)/.exec(html)?.[1]
    ?? /document\.write\("([^"]+)"\)/.exec(html)?.[1];
  if (!cipher) throw new Error("91porn player markup changed (no strencode2) — update the resolver");
  const sourceTag = decodeURIComponent(cipher);
  const url = /src='([^']+)'/i.exec(sourceTag)?.[1];
  if (!url) throw new Error("91porn page yielded no video url");

  const title = (/<title>([^<]+)<\/title>/i.exec(html)?.[1] ?? "").replace(/\s*[-–]\s*91porn\s*$/i, "").trim();
  const duration = parseHms(/时长[^<]*(?:<span[^>]*>)?\s*(\d{1,2}:\d{2}:\d{2})/.exec(html)?.[1] ?? "");
  return {
    title: title || "91porn video",
    durationSec: duration,
    url,
    headers: { "User-Agent": UA, Referer: "https://91porn.com/" },
  };
}

const RUNNER_RESOLVERS = {
  "91porn": resolve91porn,
};

export async function resolveOnRunner(site, sourceUrl) {
  const fn = RUNNER_RESOLVERS[site];
  if (!fn) throw new Error(`no runner-side resolver for site "${site}"`);
  return fn(sourceUrl);
}
