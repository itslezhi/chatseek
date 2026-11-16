import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, readFile, readdir, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Context } from '@deepseek-ai/cordis';
import SystemPrompt from '@deepseek-ai/dsh-system-prompt';
import Tools from '@deepseek-ai/dsh-tools';
import * as native from '@deepseek-ai/dsh-tool-executor-native';
import * as plugin from '../integrations/deepseek-harness/index.mjs';
import { ChatSeekBridge } from '../integrations/deepseek-harness/bridge.mjs';

async function fixture(t, options = {}) {
  const requests = [];
  const directory = await mkdtemp(join(tmpdir(), 'chatseek-harness-'));
  const context = 'synthetic-conversation-context';
  const transcript = 'Synthetic export 测试消息\n'.repeat(10000);
  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks)) : undefined;
    requests.push({ path: req.url, body, header: req.headers['x-wechat-rag'] });
    const send = (value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); };
    if (options.handler?.(req, res)) return;
    if (options.ask && ['/api/select', '/api/sync', '/api/watch', '/api/export'].includes(req.url)) {
      return send({ access_confirmation_required: true }, 409);
    }
    if (body?.context && body.context !== context) return send({ error: 'Context changed' }, 400);
    if (req.url === '/api/state') return send({ context, setup_required: false });
    if (req.url === '/api/access') return send({ mode: options.ask ? 'ask' : 'full' });
    if (req.url === '/api/history' || req.url === '/api/search') return send({ context, sources: [{ id: 'M1', text: 'Synthetic evidence 测试' }] });
    if (req.url === '/api/export') {
      res.writeHead(200, { 'Content-Disposition': 'attachment; filename="../../escape.txt"', 'Content-Length': Buffer.byteLength(transcript) });
      return res.end(transcript);
    }
    if (req.url === '/api/select') return send({ context, selected: true });
    return send({ context, watch_running: body?.enabled ?? false });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const config = { baseUrl: `http://127.0.0.1:${server.address().port}`, exportDirectory: directory, ...options.config };
  const bridge = new ChatSeekBridge(config);
  t.after(async () => {
    bridge.dispose();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  });
  return { bridge, config, requests, context, directory, transcript };
}

async function runtime(t, config) {
  const ctx = new Context();
  t.after(() => ctx.fiber.dispose());
  await ctx.plugin(SystemPrompt, {});
  await ctx.plugin(Tools);
  await ctx.plugin(native);
  const installed = await ctx.plugin(plugin, config);
  let id = 0;
  const call = async (name, args = {}, signal = new AbortController().signal) => {
    const output = await ctx.tools.execute({ calls: [{ type: 'tool-call', id: `test-${++id}`, name: `chatseek_${name}`, arguments: args }], signal });
    return output.results[0].result;
  };
  return { ctx, installed, call };
}

test('official Harness runtime registers seven tools, retrieves evidence and cleans up on unload', async t => {
  const f = await fixture(t);
  const { ctx, installed, call } = await runtime(t, f.config);
  assert.equal(ctx.tools.schemas().length, 7);
  assert.equal(f.requests.length, 0, 'activation must not access chats');
  const status = await call('status');
  assert.equal(status.isError, false);
  assert.equal(status.value.context, f.context);
  for (const [name, args] of [
    ['select', { name: 'Synthetic contact' }],
    ['history', { context: f.context, limit: 20 }],
    ['search', { context: f.context, question: '测试' }],
    ['sync', { context: f.context }], ['watch', { enabled: true }], ['watch', { enabled: false }],
  ]) {
    const result = await call(name, args);
    assert.equal(result.isError, false, JSON.stringify(result));
  }
  assert.ok(f.requests.every(r => r.header === '1' && !r.body?.access_approved));
  assert.equal(f.requests.some(r => r.path === '/api/ask'), false);
  await installed.dispose();
  assert.equal(ctx.tools.schemas().length, 0);
});

test('runtime rejects invalid arguments and prevents model-supplied access approval', async t => {
  const f = await fixture(t);
  const { call } = await runtime(t, f.config);
  for (const [name, args] of [
    ['sync', { context: f.context, access_approved: true }],
    ['history', { context: f.context, limit: 501 }], ['history', {}],
    ['export', { context: f.context, format: '../txt' }], ['watch', { enabled: 'true' }],
  ]) assert.equal((await call(name, args)).isError, true);
  assert.equal(f.requests.length, 0);
});

test('ask-mode denial is surfaced without retry or changing permission', async t => {
  const f = await fixture(t, { ask: true });
  const { call } = await runtime(t, f.config);
  const result = await call('sync', { context: f.context });
  assert.equal(result.isError, true);
  assert.match(JSON.stringify(result), /requires access confirmation/);
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].body.access_approved, undefined);
});

test('stale conversation fails without fetching or retrying a new context', async t => {
  const f = await fixture(t);
  await assert.rejects(f.bridge.json('history', { context: 'old' }), /Context changed/);
  assert.equal(f.requests.length, 1);
});

test('complete exports stream to unique local files and never return transcript content', async t => {
  const f = await fixture(t);
  const { call } = await runtime(t, f.config);
  for (const format of ['html', 'txt', 'json']) {
    const result = await call('export', { context: f.context, format });
    assert.equal(result.isError, false, JSON.stringify(result));
    assert.equal(await readFile(result.value.path, 'utf8'), f.transcript);
    assert.equal(result.value.bytes, Buffer.byteLength(f.transcript));
    assert.equal(result.value.path.startsWith(f.directory), true);
    assert.equal(result.value.path.endsWith(`.${format}`), true);
    assert.equal(JSON.stringify(result).includes('测试消息'), false);
    if (process.platform !== 'win32') assert.equal((await stat(result.value.path)).mode & 0o777, 0o600);
  }
  assert.equal((await readdir(f.directory)).length, 3);
});

test('configuration only accepts numeric loopback HTTP and absolute export paths', () => {
  for (const baseUrl of ['https://example.com', 'http://localhost:8765', 'http://127.0.0.2', 'file:///tmp/x', 'http://127.0.0.1/api', 'http://user:pass@127.0.0.1', 'http://127.0.0.1/?x=1']) {
    assert.throws(() => new ChatSeekBridge({ baseUrl }));
  }
  assert.throws(() => new ChatSeekBridge({ exportDirectory: 'relative' }));
  assert.throws(() => new ChatSeekBridge({ timeoutMs: Infinity }));
});

test('redirects are not followed', async t => {
  const f = await fixture(t, { handler: (_req, res) => { res.writeHead(302, { Location: 'http://127.0.0.1:1/forbidden' }); res.end(); return true; } });
  await assert.rejects(f.bridge.status(), /Redirects are not permitted/);
  assert.equal(f.requests.length, 1);
});

test('JSON and export response sizes are bounded', async t => {
  const f = await fixture(t, { handler: (req, res) => {
    if (req.url === '/api/export') { res.writeHead(200, { 'Content-Length': 600 * 1024 * 1024 }); res.flushHeaders(); }
    else res.end(JSON.stringify({ value: 'x'.repeat(4 * 1024 * 1024) }));
    return true;
  } });
  await assert.rejects(f.bridge.status(), /response too large/);
  await assert.rejects(f.bridge.exportHistory(f.context, 'txt'), /512 MiB/);
  assert.deepEqual(await readdir(f.directory), []);
});

test('timeout bounds an unresponsive backend', async t => {
  const f = await fixture(t, { config: { timeoutMs: 100 }, handler: () => true });
  await assert.rejects(f.bridge.status(), /timed out/);
});

test('cancel and plugin unload abort streaming exports and delete partial files', async t => {
  for (const unload of [false, true]) {
    const f = await fixture(t, { handler: (_req, res) => { res.writeHead(200); res.write('partial'); return true; } });
    const { call, installed } = await runtime(t, f.config);
    const controller = new AbortController();
    const pending = call('export', { context: f.context }, controller.signal);
    // Wait for the response to create a file, not an arbitrary cancellation delay.
    const deadline = Date.now() + 5000;
    while (!(await readdir(f.directory)).length && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal((await readdir(f.directory)).length, 1);
    if (unload) await installed.dispose(); else controller.abort();
    assert.equal((await pending).isError, true);
    assert.deepEqual(await readdir(f.directory), []);
  }
});
