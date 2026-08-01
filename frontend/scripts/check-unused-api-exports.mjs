import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const clientPath = join(root, "src", "api", "client.ts");
const client = readFileSync(clientPath, "utf8");
const exportedFunctions = [...client.matchAll(/export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(/g)].map((match) => match[1]);

function sourceFiles(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(?:ts|tsx)$/.test(name) && path !== clientPath ? [path] : [];
  });
}

const files = sourceFiles(join(root, "src"));
const unused = exportedFunctions.filter((name) => {
  const token = new RegExp(`\\b${name}\\b`);
  return !files.some((path) => token.test(readFileSync(path, "utf8")));
});

if (unused.length) {
  console.error(`Unused frontend API exports: ${unused.join(", ")}`);
  process.exit(1);
}

for (const orphan of ["features/StrategyLibrary.tsx", "features/RiskMonitor.tsx"]) {
  if (files.some((path) => relative(join(root, "src"), path).replaceAll("\\", "/") === orphan)) {
    console.error(`Orphaned feature still exists: ${orphan}`);
    process.exit(1);
  }
}

console.log(`Checked ${exportedFunctions.length} API exports; every export has a consumer.`);
