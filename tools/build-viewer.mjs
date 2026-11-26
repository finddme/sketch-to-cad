// Bundle the viewer sources into one minified IIFE that viewer.py inlines.
// Run inside Docker (never on the host), from the repo root:
//   docker run --rm -u "$(id -u)" -v "$PWD":/work -w /work/tools node:24-bookworm \
//     sh -c "npm ci && node build-viewer.mjs"
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const viewer = path.join(root, 'skills/sketch-to-cad/assets/viewer');

await build({
  entryPoints: [path.join(viewer, 'src/main.mjs')],
  outfile: path.join(viewer, 'viewer.bundle.js'),
  nodePaths: [path.join(root, 'tools/node_modules')],
  bundle: true,
  format: 'iife',
  minify: true,
  target: ['es2020'],
  legalComments: 'eof',
  logLevel: 'info',
});
