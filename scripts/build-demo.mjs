import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const output = path.join(root, 'demo/dist');
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });

// Copy only public UI assets. The Python server, credentials, and machine config
// are never part of the demo build.
for (const filename of ['index.html', 'style.css', 'app.js', 'favicon.svg']) {
  let contents = await readFile(path.join(root, 'ghr/web', filename), 'utf8');
  if (filename === 'index.html') {
    const replacements = [
      ['<title>GitHub account router</title>', '<title>GitHub account router · Interactive demo</title>\n  <meta name="description" content="Try GitHub account routing with sample accounts, repositories, and agents. No sign-in required.">'],
      ['  <script src="/app.js" defer></script>', '  <script src="/demo.js" defer></script>\n  <script src="/app.js" defer></script>'],
      ['Local configuration<svg', 'Sample configuration<svg'],
      ['    <main id="main"', `    <div class="demo-banner" aria-label="Demo information"><div><span class="demo-label">Demo</span><span>Mock data. Changes stay in this browser.</span></div><div class="demo-actions"><button id="reset-demo" class="text-button"><svg class="icon" aria-hidden="true"><use href="#i-refresh"/></svg>Reset demo</button><a class="button subtle" href="https://github.com/Sma-Das/multi-github-account#install" target="_blank" rel="noreferrer">Install ghr<svg class="icon" aria-hidden="true"><use href="#i-link"/></svg></a></div></div>\n    <main id="main"`],
    ];
    for (const [before, after] of replacements) {
      if (!contents.includes(before)) throw new Error(`Dashboard changed. Update the demo replacement: ${before}`);
      contents = contents.replace(before, after);
    }
  }
  await writeFile(path.join(output, filename), contents);
}
await writeFile(path.join(output, 'demo.js'), await readFile(path.join(root, 'demo/demo.js')));
console.log('Built the standalone demo in demo/dist.');
