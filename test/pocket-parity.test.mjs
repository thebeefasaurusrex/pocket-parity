import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const python = process.env.POCKET_PARITY_PYTHON || path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python3');
const mesen = process.env.POCKET_PARITY_MESEN;
const lcc = process.env.POCKET_PARITY_LCC;
const pngMagic = Buffer.from('89504e470d0a1a0a', 'hex');

function cli(args) {
  const result = spawnSync(process.execPath, [path.join(root, 'bin/pocket-parity.mjs'), ...args], {
    encoding: 'utf8', cwd: root, windowsHide: true,
  });
  assert.ifError(result.error);
  return result;
}

function evidence(result) {
  return result.stdout.trim().split(/\r?\n/).at(-1).replace(/^Evidence: /, '');
}

test('PocketParity CLI prints usage and rejects invalid commands', () => {
  const help = cli(['--help']);
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /Usage: pocket-parity run/);
  assert.equal(cli(['run']).status, 2);
});

test('PocketParity measures the original cartridge on both backends, catches wrong timing and rejects stale evidence', {
  skip: !existsSync(python) || !lcc || !mesen || process.platform !== 'win32'
    ? 'Set up Python, Mesen, and GBDK to exercise both emulators'
    : false,
  timeout: 90_000,
}, (t) => {
  const work = mkdtempSync(path.join(tmpdir(), 'pocket-parity-test-'));
  t.after(() => rmSync(work, { recursive: true, force: true }));
  for (const file of ['timing.c', 'timing.txt', 'wrong.txt']) {
    copyFileSync(path.join(root, 'example', file), path.join(work, file));
  }
  for (const [extension, flags] of [['gb', []], ['gbc', ['-Wm-yc']]]) {
    const built = spawnSync(lcc, [...flags, '-o', 'timing.' + extension, 'timing.c'], { cwd: work, encoding: 'utf8' });
    assert.ifError(built.error);
    assert.equal(built.status, 0, built.stdout + built.stderr);
  }
  const gb = readFileSync(path.join(work, 'timing.gb'));
  const gbc = readFileSync(path.join(work, 'timing.gbc'));
  assert.equal(gb[0x143], 0);
  assert.equal(gbc[0x143], 0x80);
  const events = [];
  for (const backend of ['pyboy', 'mesen']) {
    const run = (script) => cli(['run', 'timing.gb', script, '--backend', backend, '--cwd', work]);
    const first = run('timing.txt');
    const second = run('timing.txt');
    assert.equal(first.status, 0, first.stdout + first.stderr);
    assert.equal(second.status, 0, second.stdout + second.stderr);
    const firstDir = evidence(first);
    const secondDir = evidence(second);
    const bytes = readFileSync(path.join(firstDir, 'measure.json'));
    assert.deepEqual(bytes, readFileSync(path.join(secondDir, 'measure.json')));
    const outcome = JSON.parse(bytes);
    assert.equal(outcome.frame, 2204);
    assert.ok(first.stdout.trim().split(/\r?\n/).length <= 15);
    const find = (command) => outcome.events.find((event) => event.command === command);
    assert.deepEqual(find('read').bytes, [1, 0, 0, 24, 0, 1]);
    assert.deepEqual(find('oam').sprites, [[40, 54, 2, 0]]);
    assert.deepEqual(find('tiles').rows, [[1, 2], [1, 2]]);
    assert.deepEqual(find('diff').changes, [
      ['0xC101', 0, 1], ['0xC103', 24, 54], ['0xC104', 0, 4], ['0xC105', 1, 0],
    ]);
    assert.deepEqual(find('watch').changes, [[224, 2], [284, 3]]);
    assert.deepEqual(outcome.events.filter(({ command }) => command === 'measure').map(({ frames }) => frames), [59, 2099]);
    assert.equal(outcome.events.find(({ frame, command }) => frame === find('load').frame && command === 'load').name, 'mid');
    assert.deepEqual(readFileSync(path.join(firstDir, 'contact-sheet.png')).subarray(0, 8), pngMagic);
    assert.deepEqual(readFileSync(path.join(firstDir, 'snap-moved.png')).subarray(0, 8), pngMagic);
    assert.equal(cli(['show', firstDir]).status, 0);
    const wrong = run('wrong.txt');
    assert.equal(wrong.status, 1, wrong.stdout + wrong.stderr);
    assert.match(wrong.stdout, /line 6: Expected wrong_duration to take 11 frames, got 10/);
    const color = cli(['run', 'timing.gbc', 'timing.txt', '--backend', backend, '--cwd', work]);
    assert.equal(color.status, 0, color.stdout + color.stderr);
    const colorResult = JSON.parse(readFileSync(path.join(evidence(color), 'measure.json')));
    assert.deepEqual(colorResult.events.filter(({ command }) => command === 'measure'),
      outcome.events.filter(({ command }) => command === 'measure'));
    events.push(outcome.events);
  }
  assert.deepEqual(events[0], events[1]);
  const newest = cli(['run', 'timing.gb', 'timing.txt', '--cwd', work]);
  assert.equal(newest.status, 0, newest.stdout + newest.stderr);
  const script = path.join(work, 'timing.txt');
  writeFileSync(script, readFileSync(script, 'utf8') + '\n# changed after the run\n');
  const stale = cli(['show', evidence(newest)]);
  assert.equal(stale.status, 3, stale.stdout + stale.stderr);
  assert.match(stale.stdout, /STALE/);
  t.diagnostic('Both emulators matched all measured events and rejected the wrong frame count.');
});
