'use strict';

const { createHash } = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const desktopDir = __dirname;
const electronVersion = require('electron/package.json').version;
const cacheRoot = path.join(
  process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'),
  'electron',
  'Cache',
);
const zipName = `electron-v${electronVersion}-win32-x64.zip`;
const downloadUrl = new URL(
  `https://github.com/electron/electron/releases/download/v${electronVersion}/${zipName}`,
);
downloadUrl.pathname = path.posix.dirname(downloadUrl.pathname);
const cacheKey = createHash('sha256').update(downloadUrl.toString()).digest('hex');
const cachedZipDir = path.join(cacheRoot, cacheKey);
const cachedZipPath = path.join(cachedZipDir, zipName);

const packager = path.join(desktopDir, 'node_modules', '@electron', 'packager', 'bin', 'electron-packager.mjs');
const args = [
  packager,
  '.',
  'VEIL',
  '--platform=win32',
  '--arch=x64',
  '--out=dist',
  '--overwrite',
  '--tmpdir=.build',
  '--ignore=^/tests',
  '--ignore=^/dist',
  '--ignore=^/README.md',
  '--ignore=^/.npm-cache',
  '--ignore=^/.build',
  '--ignore=^/test-output',
  `--download.cacheRoot=${cacheRoot}`,
];

if (fs.existsSync(cachedZipPath)) {
  console.log(`Using cached Electron ${electronVersion} package.`);
  args.push(`--electron-zip-dir=${cachedZipDir}`);
} else {
  console.log(`Electron ${electronVersion} is not cached yet; the first packaging run will download it.`);
}

const result = spawnSync(process.execPath, args, { cwd: desktopDir, stdio: 'inherit' });
if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
