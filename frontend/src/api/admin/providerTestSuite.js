// Streaming client for the admin provider /test-suite endpoint.
//
// fetch() returns a ReadableStream we parse line-by-line into SSE
// `event:` / `data:` frames. We deliberately do NOT use EventSource: it
// can't send POST bodies or Authorization headers, and the test-suite
// run is parameterised + auth-required.

import { apiFetch, getApiBase, getToken } from "../client.js";

/**
 * Open a one-shot SSE stream for a provider test-suite run.
 *
 * @param {string} providerId
 * @param {{model_id: string, suites?: string[], case_ids?: string[], dry_run?: boolean}} body
 * @returns {{ on: (event:string, fn:(e:CustomEvent)=>void)=>void,
 *            off: (event:string, fn:(e:CustomEvent)=>void)=>void,
 *            abort: ()=>void }}
 */
export function startTestSuite(providerId, body) {
  const target = new EventTarget();
  const controller = new AbortController();

  (async () => {
    const apiBase = getApiBase().replace(/\/+$/, "");
    let resp;
    try {
      resp = await fetch(
        `${apiBase}/api/admin/providers/${encodeURIComponent(providerId)}/test-suite`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${getToken()}`,
            Accept: "text/event-stream",
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        },
      );
    } catch (err) {
      if (err.name !== "AbortError") {
        target.dispatchEvent(
          new CustomEvent("error", {
            detail: { message: err.message || String(err), kind: "NETWORK" },
          }),
        );
      }
      return;
    }

    if (!resp.ok) {
      let detail = "";
      try {
        detail = (await resp.text()).slice(0, 400);
      } catch {
        /* ignore */
      }
      target.dispatchEvent(
        new CustomEvent("error", {
          detail: {
            message: `HTTP ${resp.status}${detail ? ` · ${detail}` : ""}`,
            kind: "HTTP",
            status: resp.status,
          },
        }),
      );
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let event = null;
    let dataLines = [];

    const flush = () => {
      if (!event) {
        dataLines = [];
        return;
      }
      const data = dataLines.join("\n");
      dataLines = [];
      const ev = event;
      event = null;
      if (!data) return;
      try {
        const detail = JSON.parse(data);
        target.dispatchEvent(new CustomEvent(ev, { detail }));
      } catch {
        /* swallow malformed frame */
      }
    };

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, idx).replace(/\r$/, "");
          buffer = buffer.slice(idx + 1);
          if (line === "") {
            flush();
          } else if (line.startsWith("event: ")) {
            event = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            dataLines.push(line.slice(6));
          }
        }
      }
      // Flush trailing frame if present.
      buffer += decoder.decode();
      if (buffer) {
        for (const line of buffer.split("\n")) {
          if (line === "") flush();
          else if (line.startsWith("event: ")) event = line.slice(7).trim();
          else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
        }
        flush();
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        target.dispatchEvent(
          new CustomEvent("error", {
            detail: { message: err.message || String(err), kind: "STREAM" },
          }),
        );
      }
    }
  })();

  return {
    on: (event, fn) => target.addEventListener(event, fn),
    off: (event, fn) => target.removeEventListener(event, fn),
    abort: () => controller.abort(),
  };
}

/**
 * POST a manual verdict (pass / fail / skip) for a SEMI / MANUAL case.
 */
export function postManualVerdict(providerId, runId, caseId, verdict) {
  return apiFetch(
    `/api/admin/providers/${encodeURIComponent(providerId)}/test-suite/` +
      `${encodeURIComponent(runId)}/cases/${encodeURIComponent(caseId)}/verdict`,
    { method: "POST", body: { verdict } },
  );
}

/** Build the absolute URL for a persisted test-suite image. */
export function imageUrl(providerId, runId, name) {
  const base = getApiBase().replace(/\/+$/, "");
  return (
    `${base}/api/admin/providers/${encodeURIComponent(providerId)}` +
    `/test-suite/${encodeURIComponent(runId)}/images/${encodeURIComponent(name)}`
  );
}
