#!/usr/bin/env node
// Point @sinas/sdk at a published version (or a local tarball) for a
// reproducible build, keeping every other dependency at the version
// package-lock.json pins.
//
//   node scripts/use-published-sdk.mjs 0.7.0
//   node scripts/use-published-sdk.mjs file:./sinas-sdk.tgz
//
// package.json links @sinas/sdk to a sibling checkout (file:../../sinas-js)
// for local development, and package-lock.json records that link. A build
// without the checkout needs a real version instead. Rewriting only
// package.json is not enough: npm treats the recorded link as satisfying an
// exact-version spec and leaves a dangling symlink. So this rewrites the
// spec AND drops the link's entries from the lockfile; then
//
//   npm install --package-lock-only   (resolves just @sinas/sdk into the lock)
//   npm ci                            (installs exactly the lock)
//
// installs the same tree every time. Used by console/Dockerfile and the CI
// console-build job; bump the version in both together.
import { readFileSync, writeFileSync } from "node:fs";

const spec = process.argv[2];
if (!spec) {
  console.error("usage: use-published-sdk.mjs <version | file:./sinas-sdk.tgz>");
  process.exit(2);
}

const pkg = JSON.parse(readFileSync("package.json", "utf8"));
pkg.dependencies["@sinas/sdk"] = spec;
writeFileSync("package.json", JSON.stringify(pkg, null, 2) + "\n");

const lock = JSON.parse(readFileSync("package-lock.json", "utf8"));
const packages = lock.packages ?? {};
for (const key of Object.keys(packages)) {
  // the link itself, and the linked sibling checkouts it pulled in
  if (key === "node_modules/@sinas/sdk" || key.startsWith("../")) delete packages[key];
}
if (packages[""]?.dependencies) packages[""].dependencies["@sinas/sdk"] = spec;
writeFileSync("package-lock.json", JSON.stringify(lock, null, 2) + "\n");

console.log(`@sinas/sdk -> ${spec}`);
