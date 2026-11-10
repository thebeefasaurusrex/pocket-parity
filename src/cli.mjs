import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const VERSION = '0.1.0';
function mesen() {
  if (process.platform !== 'win32') throw new Error('The Mesen backend currently supports Windows.');
  if (!process.env.POCKET_PARITY_MESEN) throw new Error('Set POCKET_PARITY_MESEN to the Mesen 2.1.1 executable.');
  return realpathSync(process.env.POCKET_PARITY_MESEN);
}
const WORKER = fileURLToPath(new URL('./lab.py', import.meta.url));
const WORKER_FILES = ['cli.mjs', 'lab.py', 'backends.py', 'mesen.lua', 'requirements.txt'];
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
const hashFile = (file) => sha(readFileSync(file));
const json = (value) => JSON.stringify(value, null, 2) + '\n';

function git(cwd, ...args) {
  const result = spawnSync('git', ['-C', cwd, ...args], { encoding: 'utf8', windowsHide: true });
  return result.status === 0 ? result.stdout.trim() : null;
}

function identity(cwd, rom, script, backend) {
  const commit = git(cwd, 'rev-parse', 'HEAD');
  const status = commit ? git(cwd, 'status', '--porcelain', '--', ':/', ':(exclude,glob)**/.pocket-parity/**') : null;
  if (commit && status === null) throw new Error('Could not get git status');
  const executable = backend === 'mesen' ? mesen() : python();
  const versions = spawnSync(python(), ['-c',
    'import sys; from importlib.metadata import version; print("|".join(version(p) for p in sys.argv[1:]))',
    ...(backend === 'pyboy' ? ['pyboy', 'numpy', 'Pillow'] : ['Pillow'])],
  { encoding: 'utf8', windowsHide: true, timeout: 10_000 });
  if (versions.status !== 0) throw new Error('Install the PocketParity Python packages (see README.md)');
  return {
    git: commit ? { commit, dirty: Boolean(status) } : null,
    sourceSha256: sha(json([hashFile(rom), hashFile(script)])),
    toolSha256: sha(json(WORKER_FILES.map((file) => hashFile(new URL('./' + file, import.meta.url))))),
    engine: { path: executable, sha256: hashFile(executable), pythonPackages: versions.stdout.trim() },
  };
}

function python() {
  const binary = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python3'];
  const executable = process.env.POCKET_PARITY_PYTHON || path.join(process.cwd(), '.venv', ...binary);
  if (!existsSync(executable)) throw new Error('Create the Python 3.13 environment or set POCKET_PARITY_PYTHON (see README.md).');
  return realpathSync(executable);
}

function print(result, output, full) {
  console.log(result.status + ' PocketParity | ' + result.engineVersion + ' | frame ' + result.frame);
  if (result.error) console.log(result.error);
  const measurements = result.events.filter(({ command }) => command === 'measure');
  for (const event of measurements.slice(0, 8)) {
    console.log(event.name + ': ' + event.frames + ' frames');
  }
  const counts = new Map();
  for (const { command } of result.events) counts.set(command, (counts.get(command) ?? 0) + 1);
  console.log([...counts.keys()].sort().map((name) => counts.get(name) + ' ' + name).join(', ') || 'No results');
  if (full) for (const event of result.events) console.log(JSON.stringify(event));
  console.log('Evidence: ' + output);
}

function resultCode(status) {
  return status === 'PASS' ? 0 : status === 'FAIL' ? 1 : 2;
}

function show(output, full) {
  let data;
  try {
    const stamp = JSON.parse(readFileSync(path.join(output, 'stamp.json'), 'utf8'));
    const measure = readFileSync(path.join(output, 'measure.json'));
    if (stamp.tool !== VERSION || hashFile(path.join(output, 'contact-sheet.png')) !== stamp.contactSheetSha256 ||
        sha(measure) !== stamp.measureSha256 ||
        json(identity(stamp.cwd, stamp.rom, stamp.script, stamp.backend)) !== json(stamp.identity)) {
      throw new Error('Evidence changed');
    }
    data = JSON.parse(measure);
  } catch {
    console.log('STALE PocketParity evidence');
    console.log('Evidence: ' + output);
    return 3;
  }
  print(data, output, full);
  return resultCode(data.status);
}

export function pocketParity(args) {
  const options = parseArgs({ args, allowPositionals: true, options: {
    cwd: { type: 'string' }, backend: { type: 'string' }, full: { type: 'boolean' },
    help: { type: 'boolean', short: 'h' },
  } });
  const [action, target, scriptPath, ...extra] = options.positionals;
  const { backend = 'pyboy', full = false } = options.values;
  if (!action || options.values.help) {
    console.log('Usage: pocket-parity run <rom> <script> [--backend pyboy|mesen] [--cwd <project>] [--full]');
    console.log('       pocket-parity show <evidence folder> [--full]');
    return 0;
  }
  const cwd = realpathSync(options.values.cwd || process.cwd());
  if (action === 'show' && target && !scriptPath) return show(path.resolve(cwd, target), full);
  if (action !== 'run' || !target || !scriptPath || extra.length || !['pyboy', 'mesen'].includes(backend)) {
    throw new Error('Use pocket-parity run <rom> <script> [--backend pyboy|mesen]');
  }
  const rom = realpathSync(path.resolve(cwd, target));
  if (!['.gb', '.gbc'].includes(path.extname(rom).toLowerCase())) throw new Error('Use a .gb or .gbc cartridge.');
  const script = realpathSync(path.resolve(cwd, scriptPath));
  const before = identity(cwd, rom, script, backend);
  const evidenceRoot = path.join(cwd, '.pocket-parity');
  mkdirSync(evidenceRoot, { recursive: true });
  const output = mkdtempSync(path.join(evidenceRoot, backend + '-'));
  const started = performance.now();
  const processArgs = ['-B', WORKER, rom, script, output, '--backend', backend];
  if (full) processArgs.push('--full');
  const worker = spawnSync(python(), processArgs, {
    encoding: 'utf8', cwd: output, timeout: 200_000, windowsHide: true,
    env: { ...process.env, PYTHONUTF8: '1', ...(backend === 'mesen' ? { POCKET_PARITY_MESEN: before.engine.path } : {}) },
  });
  writeFileSync(path.join(output, 'worker.log'), (worker.stdout || '') + (worker.stderr || ''));
  const measure = path.join(output, 'measure.json');
  if (!existsSync(measure)) {
    const firstLine = worker.stdout?.trim().split(/\r?\n/, 1)[0];
    console.log(firstLine || 'ERROR PocketParity: ' + (worker.error?.message ?? 'worker did not write results'));
    console.log('Evidence: ' + output);
    return 2;
  }
  const data = JSON.parse(readFileSync(measure, 'utf8'));
  const after = identity(cwd, rom, script, backend);
  writeFileSync(path.join(output, 'stamp.json'), json({
    tool: VERSION, backend, cwd, rom, script, args, identity: before,
    engine: { ...before.engine, version: data.engineVersion },
    renderer: 'headless', device: null, seed: null, wallTimeMs: Math.round(performance.now() - started),
    measureSha256: hashFile(measure), contactSheetSha256: hashFile(path.join(output, 'contact-sheet.png')),
  }));
  if (json(before) !== json(after)) {
    console.log('STALE PocketParity evidence (project inputs changed during the run)');
    console.log('Evidence: ' + output);
    return 3;
  }
  print(data, output, full);
  return resultCode(data.status);
}
