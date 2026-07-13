// ── SSE client for POST streaming endpoints ───────────────────
//
// Native EventSource is GET-only, so the streaming AI endpoints
// (POST /api/query/stream, /api/dashboards/generate/stream, ...)
// are consumed with fetch() + a ReadableStream SSE parser.
// See docs/sse-events.md for the event contract.

import { API_BASE_URL, API_TOKEN } from "./client";

/**
 * Thrown when the stream endpoint cannot be reached or refuses the
 * request (network failure, 404 because the backend doesn't support
 * streaming yet, auth errors, ...). Callers catch this to fall back
 * to the equivalent non-streaming endpoint.
 */
export class SSEConnectionError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "SSEConnectionError";
    this.status = status;
  }
}

export type SSEEventHandler = (type: string, data: unknown) => void;

/** Parse one SSE frame (the text between two blank lines) and dispatch it. */
function dispatchFrame(frame: string, onEvent: SSEEventHandler) {
  let type = "message";
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    // Comment / heartbeat lines start with ":".
    if (!line || line.startsWith(":")) continue;

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") {
      type = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
    // Other fields (id, retry, ...) are not used by this contract.
  }

  if (dataLines.length === 0) return; // comment-only frame (heartbeat)

  const raw = dataLines.join("\n");
  let data: unknown = raw;
  try {
    data = JSON.parse(raw);
  } catch {
    // Defensive: keep the raw string if the payload isn't valid JSON.
  }

  // Every event — including unknown types — is forwarded (forward-compat).
  onEvent(type, data);
}

/**
 * POST `body` to `path` and consume the response as a Server-Sent-Events
 * stream. `onEvent` is invoked for every event in order; the returned
 * promise resolves when the server closes the stream.
 *
 * Throws SSEConnectionError if the connection cannot be established
 * (so callers can fall back to the non-streaming endpoint).
 */
export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: SSEEventHandler,
  signal?: AbortSignal
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        Authorization: `Bearer ${API_TOKEN}`,
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new SSEConnectionError(
      `Could not connect to ${path}: ${(err as Error).message}`
    );
  }

  if (!response.ok) {
    // Drain the body so the connection can be reused.
    response.body?.cancel().catch(() => undefined);
    throw new SSEConnectionError(
      `Stream endpoint ${path} responded with HTTP ${response.status}`,
      response.status
    );
  }

  if (!response.body) {
    throw new SSEConnectionError(`Stream endpoint ${path} returned no body`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");

      // Frames are separated by a blank line.
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        if (frame.trim()) dispatchFrame(frame, onEvent);
      }
    }

    // Flush anything the server sent without a trailing blank line.
    buffer += decoder.decode();
    buffer = buffer.replace(/\r\n/g, "\n");
    if (buffer.trim()) dispatchFrame(buffer, onEvent);
  } finally {
    reader.cancel().catch(() => undefined);
  }
}

export interface SSETerminalEvent {
  type: string;
  data: unknown;
}

/**
 * Run one streaming AI operation: progress events go to `onProgress`,
 * and the promise resolves with the terminal event (one of
 * `terminalTypes`, or `error`). Rejects with SSEConnectionError when
 * the endpoint is unreachable, so the caller can fall back to the
 * non-streaming API.
 */
export async function streamAIOperation(
  path: string,
  body: unknown,
  terminalTypes: string[],
  onProgress: SSEEventHandler,
  signal?: AbortSignal
): Promise<SSETerminalEvent> {
  let terminal: SSETerminalEvent | null = null;

  await streamSSE(
    path,
    body,
    (type, data) => {
      if (terminal) return; // anything after the terminal event is ignored
      if (type === "error" || terminalTypes.includes(type)) {
        terminal = { type, data };
      } else {
        onProgress(type, data);
      }
    },
    signal
  );

  if (!terminal) {
    throw new Error("Stream ended without a terminal event");
  }
  return terminal;
}
