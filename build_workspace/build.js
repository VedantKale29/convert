// Bundles one generated app. Usage: node build.js <jobDir>
// Expects <jobDir>/App.jsx and <jobDir>/styles.css; writes <jobDir>/dist/entry.js + entry.css.
const path = require('path');
const fs = require('fs');
const esbuild = require('esbuild');

const jobDir = path.resolve(process.argv[2]);
fs.writeFileSync(path.join(jobDir, 'entry.jsx'),
  "import React from 'react';\nimport { createRoot } from 'react-dom/client';\nimport App from './App.jsx';\n" +
  "createRoot(document.getElementById('root')).render(<App />);\n");

const ALLOWED = new Set(['react', 'react-dom/client', './App.jsx', './styles.css']);
const allowlist = {
  name: 'import-allowlist',
  setup(build) {
    build.onResolve({ filter: /.*/ }, (args) => {
      const fromGenerated = args.importer.startsWith(jobDir);
      if (fromGenerated && !ALLOWED.has(args.path)) {
        return { errors: [{ text: `import '${args.path}' is not allowed` }] };
      }
      return undefined;
    });
  },
};

esbuild.build({
  entryPoints: [path.join(jobDir, 'entry.jsx')],
  bundle: true, minify: true, outdir: path.join(jobDir, 'dist'),
  loader: { '.jsx': 'jsx' }, define: { 'process.env.NODE_ENV': '"production"' },
  nodePaths: [path.join(__dirname, 'node_modules')], plugins: [allowlist],
  logLevel: 'silent', target: ['es2019'],
}).then(() => {
  process.stdout.write(JSON.stringify({ ok: true }));
}).catch((e) => {
  const errors = (e.errors || []).map((m) => (m.location ? `${path.basename(m.location.file)}:${m.location.line}: ` : '') + m.text);
  process.stdout.write(JSON.stringify({ ok: false, errors: errors.length ? errors : [String(e)] }));
  process.exit(1);
});
