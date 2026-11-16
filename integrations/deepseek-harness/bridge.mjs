import { randomUUID } from 'node:crypto';
import { mkdir, open, rm } from 'node:fs/promises';
import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';

const JSON_LIMIT = 4 * 1024 * 1024;
const EXPORT_LIMIT = 512 * 1024 * 1024;

export class ChatSeekBridge {
  constructor(config = {}) {
    const url = new URL(config.baseUrl ?? 'http://127.0.0.1:8765');
    // Numeric loopback avoids DNS/proxy ambiguity. Never follow redirects.
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' ||
        url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
      throw new Error('ChatSeek baseUrl must be http://127.0.0.1:<port> with no path or credentials.');
    }
    this.baseUrl = url.origin;
    this.exportDirectory = config.exportDirectory ?? join(homedir(), 'ChatSeek', 'exports');
    if (typeof this.exportDirectory !== 'string' || !isAbsolute(this.exportDirectory)) {
      throw new Error('ChatSeek exportDirectory must be an absolute local path.');
    }
    this.timeoutMs = config.timeoutMs ?? 120_000;
    if (!Number.isInteger(this.timeoutMs) || this.timeoutMs < 100 || this.timeoutMs > 600_000) {
      throw new Error('ChatSeek timeoutMs must be an integer from 100 to 600000.');
    }
    this.lifecycle = new AbortController();
  }

  dispose() { this.lifecycle.abort(); }

  async request(action, data, signal, consume) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new Error('ChatSeek request timed out.')), this.timeoutMs);
    const combined = AbortSignal.any([controller.signal, this.lifecycle.signal, ...(signal ? [signal] : [])]);
    let response;
    try {
      combined.throwIfAborted();
      response = await fetch(`${this.baseUrl}/api/${action}`, {
        method: data === undefined ? 'GET' : 'POST',
        headers: { 'X-WeChat-RAG': '1', 'Content-Type': 'application/json' },
        body: data === undefined ? undefined : JSON.stringify(data),
        redirect: 'error', signal: combined,
      });
      if (!response.ok) {
        const detail = await readJson(response, combined);
        if (response.status === 409 && detail.access_confirmation_required) {
          throw new Error(`ChatSeek requires access confirmation. Open ${this.baseUrl} and perform this action there, or personally enable full access in ChatSeek and retry. The plugin cannot grant access.`);
        }
        throw new Error(`ChatSeek HTTP ${response.status}: ${String(detail.error ?? 'Request failed').slice(0, 500)}`);
      }
      return await consume(response, combined);
    } catch (error) {
      if (combined.aborted) throw combined.reason;
      if (error instanceof TypeError) {
        throw new Error(`Cannot connect to ChatSeek at ${this.baseUrl}. Start ChatSeek on the same computer as the Harness host and complete its setup. Redirects are not permitted.`);
      }
      throw error;
    } finally {
      clearTimeout(timer);
      if (response?.body && !response.body.locked) await response.body.cancel().catch(() => {});
    }
  }

  json(action, data, signal) { return this.request(action, data, signal, readJson); }

  async status(signal) {
    const state = await this.json('state', undefined, signal);
    const access = await this.json('access', undefined, signal);
    return { ...state, access, url: this.baseUrl };
  }

  async exportHistory(context, format, signal) {
    if (!['html', 'txt', 'json'].includes(format)) throw new Error('Export format must be html, txt or json.');
    return this.request('export', { context, format }, signal, async (response, activeSignal) => {
      const declaredSize = Number(response.headers.get('content-length'));
      if (declaredSize > EXPORT_LIMIT) throw new Error('Export exceeds the 512 MiB plugin limit; use ChatSeek in the browser.');
      await mkdir(this.exportDirectory, { recursive: true, mode: 0o700 });
      // Never use a model-supplied filename or the server's Content-Disposition.
      const path = join(this.exportDirectory, `chatseek-${randomUUID()}.${format}`);
      const file = await open(path, 'wx', 0o600);
      let complete = false;
      let bytes = 0;
      try {
        for await (const chunk of response.body) {
          activeSignal.throwIfAborted();
          bytes += chunk.length;
          if (bytes > EXPORT_LIMIT) throw new Error('Export exceeds the 512 MiB plugin limit; use ChatSeek in the browser.');
          // FileHandle.write may make a short write.
          let offset = 0;
          while (offset < chunk.length) {
            activeSignal.throwIfAborted();
            const written = await file.write(chunk, offset, chunk.length - offset);
            if (written.bytesWritten === 0) throw new Error('Could not write export file.');
            offset += written.bytesWritten;
          }
        }
        activeSignal.throwIfAborted();
        if (response.headers.has('content-length') && bytes !== declaredSize) throw new Error('Incomplete export response.');
        await file.sync();
        activeSignal.throwIfAborted();
        complete = true;
        return { path, bytes, format, context, scope: 'All readable local history at export start; media files are not bundled.' };
      } finally {
        try { await file.close(); }
        finally { if (!complete) await rm(path, { force: true }); }
      }
    });
  }
}

async function readJson(response, signal) {
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    signal.throwIfAborted();
    size += chunk.length;
    if (size > JSON_LIMIT) throw new Error('ChatSeek response too large; request fewer messages or use export.');
    chunks.push(chunk);
  }
  const result = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('Invalid ChatSeek response.');
  return result;
}
