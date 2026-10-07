// Import rules of the component builder (run: node --test builder_server.test.js,
// with esbuild installed). A component is one file and may import only the
// allowlisted packages: a relative or absolute path would read the builder's
// own files into the bundle.
const test = require("node:test");
const assert = require("node:assert");
const { compileSource } = require("./builder_server.js");

const ok = async (source) => {
  const result = await compileSource(source);
  assert.strictEqual(result.success, true, JSON.stringify(result.errors));
  return result;
};
const refused = async (source, needle) => {
  const result = await compileSource(source);
  assert.strictEqual(result.success, false);
  assert.match(result.errors[0].text, needle);
};

test("allowlisted imports compile, as externals", async () => {
  const { bundle } = await ok(
    'import React from "react"; import { Button } from "@sinas/ui"; import { createRoot } from "react-dom/client";\n' +
      "export default () => React.createElement(Button, null, String(!!createRoot));"
  );
  assert.match(bundle, /__SinasComponent__/);
  assert.match(bundle, /require\("react"\)/);
});

test("relative imports are refused", async () => {
  await refused('import x from "./server.js"; export default () => x;', /single file/);
  await refused('import x from "../package.json"; export default () => x;', /single file/);
});

test("absolute imports are refused", async () => {
  await refused('import x from "/etc/passwd"; export default () => x;', /single file/);
});

test("other packages and URLs are refused", async () => {
  await refused('import x from "lodash"; export default () => x;', /not allowed/);
  await refused('import x from "https://evil.example/x.js"; export default () => x;', /not allowed/);
});
