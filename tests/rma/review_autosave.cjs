const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../../src/bareum_ai/dataset/rma/review.html'), 'utf8');
const source = html.slice(html.indexOf('function changed()'), html.indexOf('// ---- 근거 선택'));

function setup(fetch) {
  const timers = new Map();
  const events = {};
  const status = {};
  let timerId = 0;
  const documents = ['A', 'B'].map(id => ({ meta: { document_id: id, snapshot: { snapshot_id: id } } }));
  const context = vm.createContext({
    documents, selected: 0, value: 1,
    fetch, Date,
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    window: { addEventListener(name, fn) { events[name] = fn; } },
    $: () => status,
    renderSide() {}, renderDoc() {}, renderTabs() {},
  });
  vm.runInContext(`
    const pendingSaves = new Map();
    let saveQueue = Promise.resolve();
    function doc() { return documents[selected]; }
    function documentOut() { return {value}; }
    function responses() { return {}; }
    ${source}
  `, context);
  return {
    context, status, events,
    edit(selected, value) { context.selected = selected; context.value = value; context.changed(); },
    flush() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(fn => fn()); },
    wait() { return vm.runInContext('saveQueue', context); },
    pending() { return vm.runInContext('pendingSaves.size', context); },
  };
}
const ok = () => ({ ok: true, json: async () => ({ valid: true }) });

test('switching tabs preserves both documents and the edited content', async () => {
  const sent = [];
  const ui = setup(async (_, options) => { sent.push(JSON.parse(options.body)); return ok(); });
  ui.edit(0, 1);
  ui.edit(1, 2);
  ui.flush();
  await ui.wait();
  assert.deepEqual(sent.map(b => [b.document_id, b.document_response.value]), [['A', 1], ['B', 2]]);
  assert.equal(ui.pending(), 0);
});

test('edits during an in-flight save are sent in order and remain dirty until saved', async () => {
  const sent = [];
  let release;
  const ui = setup(async (_, options) => {
    sent.push(JSON.parse(options.body).document_response.value);
    if (sent.length === 1) await new Promise(resolve => { release = resolve; });
    return ok();
  });
  ui.edit(0, 1);
  ui.flush();
  await Promise.resolve();
  const first = ui.wait();
  ui.edit(0, 2);
  release();
  await first;
  assert.equal(ui.pending(), 1);
  ui.flush();
  await ui.wait();
  assert.deepEqual(sent, [1, 2]);
  assert.equal(ui.pending(), 0);
});

test('network and server failures preserve dirty state and a later edit retries', async () => {
  for (const mode of ['network', 'server']) {
    let calls = 0;
    const ui = setup(async () => {
      if (++calls === 1) {
        if (mode === 'network') throw new Error('offline');
        return { ok: false, json: async () => ({ error: '명세 불일치' }) };
      }
      return ok();
    });
    ui.edit(0, 1);
    ui.flush();
    await ui.wait();
    assert.match(ui.status.textContent, /저장 실패/);
    let warned = false;
    ui.events.beforeunload({ preventDefault() { warned = true; } });
    assert.equal(warned, true);
    ui.edit(0, 2);
    ui.flush();
    await ui.wait();
    assert.equal(ui.pending(), 0);
    warned = false;
    ui.events.beforeunload({ preventDefault() { warned = true; } });
    assert.equal(warned, false);
  }
});
