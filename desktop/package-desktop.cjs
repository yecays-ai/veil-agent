'use strict';

const { createHash } = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const desktopDir = __dirname;
const electronVersion = require('electron/package.json').version;
function mirrorOption(name, fallback) {
  const snakeName = name.replace(/([a-z])([A-Z])/g, (_, a, b) => `${a}_${b}`).toLowerCase();
  const candidates = [
    process.env[`npm_config_electron_${name.toLowerCase()}`],
    process.env[`NPM_CONFIG_ELECTRON_${snakeName.toUpperCase()}`],
    process.env[`npm_config_electron_${snakeName}`],
    process.env[`npm_package_config_electron_${name}`],
    process.env[`npm_package_config_electron_${snakeName}`],
    process.env[`ELECTRON_${snakeName.toUpperCase()}`],
  ];
  return candidates.find(Boolean) || fallback;
}

const artifactVersion = mirrorOption('customVersion', electronVersion).replace(/^v?/, 'v');
const cacheRoot = path.join(
  process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'),
  'electron',
  'Cache',
);
const zipName = mirrorOption('customFilename', `electron-${artifactVersion}-win32-x64.zip`);
const customDir = mirrorOption('customDir', artifactVersion).replace(
  '{{ version }}',
  artifactVersion.replace(/^v/, ''),
);
const downloadBase = mirrorOption('mirror', 'https://github.com/electron/electron/releases/download/');
const downloadUrl = new URL(`${downloadBase}${customDir}/${zipName}`);
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
