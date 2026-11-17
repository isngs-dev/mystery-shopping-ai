const test = require('node:test')
const assert = require('node:assert')
const d = require('../lib/detect.js')

test('RAW / edited file names follow the Drive sync rules', () => {
  assert.deepStrictEqual(d.parseName('3353921_1.pdf'), { code: '3353921', number: 1, kind: 'raw' })
  assert.deepStrictEqual(d.parseName('3353921_2.pdf'), { code: '3353921', number: 2, kind: 'edited' })
  assert.strictEqual(d.parseName('131227-1.pdf').kind, 'raw')
  assert.strictEqual(d.parseName('42386 - 1.pdf').kind, 'raw')
  assert.strictEqual(d.parseName('23695714 (2).pdf').kind, 'edited')
  assert.strictEqual(d.parseName('3353196 - Admin Shop Review-2.pdf').kind, 'edited')
  assert.deepStrictEqual(d.parseName('3353722 - Admin Shop Review.pdf'), { code: '3353722', number: 1, kind: 'raw' })   // no number = RAW
  assert.strictEqual(d.parseName('final report copy.pdf'), null)
  assert.strictEqual(d.parseName('notes.docx'), null)
})

test('file name and id come from the Drive tab', () => {
  assert.strictEqual(d.nameFromTitle('3353921_1.pdf - Google Drive'), '3353921_1.pdf')
  assert.strictEqual(d.nameFromTitle('My Drive - Google Drive'), null)
  assert.strictEqual(d.driveIdFromUrl('https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=drive_link'), '1AbCdEfGhIjKlMnOp')
  assert.strictEqual(d.driveIdFromUrl('https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp'), '1AbCdEfGhIjKlMnOp')
  assert.strictEqual(d.driveIdFromUrl('https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp'), null)
})

test('word diff marks only what changed', () => {
  const out = d.diffWords('The cashier greet me warmly', 'The cashier greeted me warmly')
  assert.deepStrictEqual(out.filter((x) => x.t !== 'same').map((x) => x.t + ':' + x.w), ['del:greet', 'add:greeted'])
  assert.deepStrictEqual(d.diffWords('a b', 'a b').map((x) => x.t), ['same', 'same'])
})

test('highlight boxes become percentages of the page', () => {
  const r = d.boxToPercent({ x0: 61.2, y0: 79.2, x1: 306, y1: 99.2 }, { width: 612, height: 792 })
  assert.strictEqual(Math.round(r.left), 10)
  assert.strictEqual(Math.round(r.top), 10)
  assert.strictEqual(d.boxToPercent(null, { width: 1, height: 1 }), null)
})
