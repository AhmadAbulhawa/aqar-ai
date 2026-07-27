import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 900;

const BACKEND_ORIGIN = (
  process.env.BACKEND_ORIGIN || "http://127.0.0.1:8000"
).replace(/\/$/, "");

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

const hopByHopHeaders = [
  "connection",
  "content-encoding",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];

async function proxyRequest(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const target = new URL(`${BACKEND_ORIGIN}/${path.map(encodeURIComponent).join("/")}`);
  target.search = request.nextUrl.search;

  const headers = new Headers(request.headers);
  for (const name of hopByHopHeaders) headers.delete(name);
  headers.delete("accept-encoding");

  try {
    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
    });
    const responseHeaders = new Headers(upstream.headers);
    for (const name of hopByHopHeaders) responseHeaders.delete(name);
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown proxy error";
    return Response.json(
      {
        detail: {
          message: "The frontend lost its connection to the backend before the request completed.",
          proxy_error: message,
          retryable: true,
        },
      },
      { status: 502 },
    );
  }
}

export const GET = proxyRequest;
export const POST = proxyRequest;
export const PUT = proxyRequest;
export const PATCH = proxyRequest;
export const DELETE = proxyRequest;
export const HEAD = proxyRequest;
export const OPTIONS = proxyRequest;
