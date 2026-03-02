/**
 * SINAS Component Builder Server
 *
 * Lightweight Node.js HTTP server that compiles TSX components using esbuild.
 * Enforces an import allowlist for security and outputs IIFE bundles.
 */
const http = require("http");
const esbuild = require("esbuild");

const ALLOWED_IMPORTS = new Set([
  "react",
  "react-dom",
  "react-dom/client",
  "@sinas/sdk",
  "@sinas/ui",
]);

/**
 * esbuild plugin that enforces the import allowlist.
 * Blocks any imports not in ALLOWED_IMPORTS.
 */
const importAllowlistPlugin = {
  name: "import-allowlist",
  setup(build) {
    // Mark allowed bare imports as external (resolved at runtime via importmap)
    build.onResolve({ filter: /.*/ }, (args) => {
      // Allow relative imports (./foo, ../bar)
      if (args.path.startsWith(".") || args.path.startsWith("/")) {
        return null; // Let esbuild handle normally
      }

      // Check if the import is in the allowlist
      const basePkg = args.path.startsWith("@")
        ? args.path.split("/").slice(0, 2).join("/")
        : args.path.split("/")[0];

      if (ALLOWED_IMPORTS.has(args.path) || ALLOWED_IMPORTS.has(basePkg)) {
        return { path: args.path, external: true };
      }

      // Block everything else
      return {
        errors: [
          {
            text: `Import "${args.path}" is not allowed. Allowed imports: ${[...ALLOWED_IMPORTS].join(", ")}`,
          },
        ],
      };
    });
  },
};

async function compileSource(sourceCode) {
  try {
    const result = await esbuild.build({
      stdin: {
        contents: sourceCode,
        loader: "tsx",
        resolveDir: "/app",
      },
      bundle: true,
      format: "iife",
      globalName: "__SinasComponent__",
      sourcemap: true,
      write: false,
      outfile: "component.js",
      target: ["es2020"],
      jsx: "transform",
      plugins: [importAllowlistPlugin],
      logLevel: "silent",
    });

    const bundle = result.outputFiles.find((f) => !f.path.endsWith(".map"));
    const sourceMap = result.outputFiles.find((f) => f.path.endsWith(".map"));

    return {
      success: true,
      bundle: bundle ? bundle.text : "",
      sourceMap: sourceMap ? sourceMap.text : null,
    };
  } catch (error) {
    const errors = error.errors
      ? error.errors.map((e) => ({
          text: e.text,
          location: e.location
            ? {
                line: e.location.line,
                column: e.location.column,
              }
            : null,
        }))
      : [{ text: error.message, location: null }];

    return {
      success: false,
      errors,
    };
  }
}

const server = http.createServer(async (req, res) => {
  // Health check
  if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok" }));
    return;
  }

  if (req.method !== "POST" || req.url !== "/compile") {
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "Not found" }));
    return;
  }

  let body = "";
  req.on("data", (chunk) => {
    body += chunk;
  });

  req.on("end", async () => {
    try {
      const { sourceCode } = JSON.parse(body);
      if (!sourceCode) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "sourceCode is required" }));
        return;
      }

      const result = await compileSource(sourceCode);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(result));
    } catch (error) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          success: false,
          errors: [{ text: `Server error: ${error.message}`, location: null }],
        })
      );
    }
  });
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log(`SINAS Builder Server listening on port ${PORT}`);
});
