import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, join } from 'node:path';
import { ApplicationError, commandMain, invokeCLI, privateDirectory } from './adapter-runtime.mjs';
const root = fileURLToPath(new URL('../', import.meta.url));
const cases = new Set(JSON.parse(readFileSync(new URL('../data/manifest.json', import.meta.url))).cases.map(item => item.id));
export async function runApplication(input, parameters, signal, invocation) {
  if (Object.keys(input).some(key => !['caseId', 'action', 'allowBook', 'allowCancel'].includes(key)) || !cases.has(input.caseId) ||
      !['run', 'inspect'].includes(input.action) || typeof input.allowBook !== 'boolean' || typeof input.allowCancel !== 'boolean') throw new Error('Select a reviewed case/action and independent literal booking/cancellation grants.');
  const directory = await privateDirectory(root, invocation), db = join(directory, 'calendar.db');
  const cli = ['uv', 'run', '--locked', '--no-sync', 'schedule-meeting', '--db', db];
  try {
    await invokeCLI([...cli, 'init', '--case', input.caseId], { cwd: root, signal });
    const argv = input.action === 'inspect' ? [...cli, 'show'] : [...cli, 'run', '--case', input.caseId, '--output-root', join(directory, 'receipts')];
    if (input.action === 'run') {
      if (input.allowBook) argv.push('--allow-book');
      if (input.allowCancel) argv.push('--allow-cancel');
      if (parameters.model) argv.push('--model', parameters.model);
    }
    return { executionKind: input.action === 'inspect' ? 'application_state_inspection' : 'native_agent',
      application: await invokeCLI(argv, { cwd: root, signal }), localState: { directory, databaseFile: db } };
  } catch (error) { if (error instanceof ApplicationError) error.evidence.runDirectory = directory; throw error; }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await commandMain(runApplication);
