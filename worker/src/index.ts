export interface Env {
  BUCKET: R2Bucket;
  ALLOWED_ORIGINS?: string;
}

function corsHeaders(origin: string = '*'): Record<string, string> {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'Range, Content-Type',
    'Access-Control-Max-Age': '86400',
    'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, ETag',
  };
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin') || '*';
    const cors = corsHeaders(origin);

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors });
    }

    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response('Method Not Allowed', { status: 405, headers: cors });
    }

    let key: string;
    try {
      key = decodeURIComponent(url.pathname.replace(/^\/+/, ''));
    } catch {
      return new Response('Bad Request', { status: 400 });
    }

    // Root status endpoint
    if (!key || key === '') {
      return new Response(
        JSON.stringify({
          status: 'ok',
          service: 'fracfyi-discover-proxy',
          bucket: 'fracfyi-discover',
        }),
        { headers: { 'Content-Type': 'application/json', ...cors } },
      );
    }

    const ifNoneMatch = request.headers.get('If-None-Match');
    const object = await env.BUCKET.get(key, {
      onlyIf: ifNoneMatch ? { etagDoesNotMatch: ifNoneMatch } : undefined,
    });

    if (!object) {
      return new Response('Not Found', { status: 404, headers: cors });
    }

    const headers = new Headers(cors);
    headers.set('Content-Type', object.httpMetadata?.contentType || 'application/json');
    headers.set('Cache-Control', 'public, max-age=3600, stale-while-revalidate=86400');
    if (object.httpEtag) headers.set('ETag', object.httpEtag);

    // 304 Not Modified if ETag matches
    if (!('body' in object)) {
      return new Response(null, { status: 304, headers });
    }

    headers.set('Content-Length', object.size.toString());
    if (request.method === 'HEAD') {
      return new Response(null, { status: 200, headers });
    }

    return new Response(object.body, { status: 200, headers });
  },
};
