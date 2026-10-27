// 실제 HTML의 상태 변환·이벤트를 실행한다. DOM 배치는 이 테스트의 범위가 아니다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const html = fs.readFileSync(path.join(__dirname, '../../src/bareum_ai/dataset/rma/review.html'), 'utf8');
const source = html.slice(html.indexOf('function emptySpan('), html.indexOf('// ---- 저장'))
  + html.slice(html.indexOf('function issueBox('), html.indexOf('// ---- 탭'));

function el(tag, attrs = {}, ...children) {
  return { tag, attrs, children: children.flat().filter(x => x != null && x !== false),
    append(...kids) { this.children.push(...kids.flat().filter(x => x != null && x !== false)); } };
}
function nodes(root, predicate) {
  if (!root || typeof root !== 'object') return [];
  return [...(predicate(root) ? [root] : []), ...root.children.flatMap(n => nodes(n, predicate))];
}
function setup(state) {
  const d = state.documents[0];
  let labels;
  const ctx = vm.createContext({
    S: state, D: 0, el, doc: () => d, block: id => d.blocks.find(b => b.id === id), lab: () => labels,
    changed() {}, renderDoc() {}, unitIssues: () => [],
    takePending: () => ({quote: '관련 조례', occurrence: null, row: null, col: null}),
  });
  vm.runInContext(source, ctx);
  labels = ctx.fromSaved(d);
  return { ctx, d, labels };
}
function example() {
  return { references: [], moves: {GROUNDS: {name: '근거', steps: ['ORDINANCE']}, ACTION: {name: '계획', steps: []}},
    documents: [{blocks: [{id: 'b', kind: 'para', text: '관련 조례'}, {id: 't', kind: 'table', rows: [[{text:'값'}]]}],
      requests: [{request_id: 'r', target_ids: ['b', 't']}], saved: {}, references: {}}] };
}
function annotation() {
  return { move: 'GROUNDS', evidence_span: {quote:'관련 조례'}, context_block_ids: [],
    steps: [{step:'ORDINANCE', evidence_span:{quote:'관련 조례'}}],
    step_status: 'partial', step_reason: '종류 미확정', spec_items: [] };
}
function annotationPanel(ui, ann) {
  const side = el('div');
  ui.ctx.renderAnnotations(side, {annotations:[ann], primary:0}, 'b', null);
  return side;
}
function select(side, value) {
  return nodes(side, n => n.tag === 'select' && n.children.some(c => c.attrs?.value === value))[0];
}
function copyButton(side) {
  return nodes(side, n => n.tag === 'button' && n.children.includes('이 판정으로 채우기'))[0];
}

if (process.argv.includes('--copy')) {
  // Python 통합 테스트에서 실제 기준 라벨 복사→응답 직렬화 경로를 호출한다.
  const ui = setup(JSON.parse(fs.readFileSync(0, 'utf8')));
  let side = el('div');
  ui.ctx.renderDocumentPanel(side);
  copyButton(side).attrs.onclick();
  for (const id of ui.d.requests.flatMap(r => r.target_ids)) {
    const b = ui.d.blocks.find(b => b.id === id);
    if (b.kind === 'table') {
      const ref = ui.ctx.refUnit('model', id).table;
      const table = ui.ctx.unitOf(id);
      Object.assign(table, {table_status:ref.table_status, table_reason:ref.table_reason,
        header_rows:ref.header_rows, row_header_cols:ref.row_header_cols});
      for (const r of ref.rows) {
        side = el('div'); ui.ctx.renderTableRow(side, id, r.row);
        copyButton(side).attrs.onclick();
      }
    } else {
      side = el('div'); ui.ctx.renderUnit(side, id);
      copyButton(side).attrs.onclick();
    }
  }
  process.stdout.write(JSON.stringify({document_id:ui.d.meta.document_id, snapshot_id:ui.d.meta.snapshot.snapshot_id,
    document_response:ui.ctx.documentOut(), block_responses:ui.ctx.responses()}));
} else {
  test('status dropdown clears stale reason, including empty steps without inventing one', () => {
    for (const previous of ['partial', 'undetermined']) {
      const ui = setup(example()), a = annotation();
      a.step_status = previous;
      if (previous === 'undetermined') a.steps = [];
      select(annotationPanel(ui, a), 'determined').attrs.onchange({target:{value:'determined'}});
      assert.equal(a.step_reason, null);
      assert.equal(ui.ctx.annotationsOut([a], '')[0].step_reason, null);
      assert.equal(a.steps.length, previous === 'partial' ? 1 : 0);
    }
  });
  test('checkbox automatic transitions and Move changes clean reasons', () => {
    const ui = setup(example()), a = annotation();
    a.steps = []; a.step_status = 'undetermined';
    const checkbox = side => nodes(side, n => n.attrs.type === 'checkbox')[0];
    checkbox(annotationPanel(ui, a)).attrs.onchange({target:{checked:true}});
    assert.equal(a.step_status, 'determined'); assert.equal(a.step_reason, null);
    checkbox(annotationPanel(ui, a)).attrs.onchange({target:{checked:false}});
    assert.equal(a.step_status, 'undetermined'); assert.equal(a.step_reason, null);
    a.step_reason = '이전 Move 사유';
    select(annotationPanel(ui, a), 'ACTION').attrs.onchange({target:{value:'ACTION'}});
    assert.equal(a.step_status, 'not_applicable'); assert.equal(a.step_reason, null);
    assert.equal(a.steps.length, 0);
  });
  test('serialization cleans legacy reasons without mutating copied annotations', () => {
    const ui = setup(example());
    for (const status of ['determined', 'not_applicable', 'partial', 'undetermined']) {
      const a = annotation(); a.step_status = status;
      const output = ui.ctx.annotationsOut([a], '')[0];
      assert.equal(output.step_reason, ['determined','not_applicable'].includes(status) ? null : a.step_reason);
      assert.equal(a.step_reason, '종류 미확정');
    }
  });
  test('table status events preserve rows in session and omit them on unreadable reload', () => {
    const ui = setup(example()), t = ui.ctx.unitOf('t');
    ui.ctx.unitOf('t', 0).annotations = [annotation()];
    function change(status) {
      const side = el('div'); ui.ctx.renderTable(side, 't');
      select(side, status).attrs.onchange({target:{value:status}});
    }
    change('unreadable'); t.table_reason = '행 구조 미확정';
    const saved = ui.ctx.blockOut('t');
    assert.equal(saved.table.rows.length, 0);
    assert.equal(t.rows[0].annotations.length, 1);
    const reload = ui.ctx.fromSaved({saved:{block_responses:{r:{blocks:[saved]}}}});
    assert.equal(Object.keys(reload.blocks.t.table.rows).length, 0);
    change('labeled');
    assert.equal(t.table_reason, null); assert.equal(t.rows[0].annotations.length, 1);
    t.table_reason = '과거 사유';
    assert.equal(ui.ctx.blockOut('t').table.table_reason, null);
  });
}
