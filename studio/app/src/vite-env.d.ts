/// <reference types="vite/client" />

// The studio-runtime package YAML is bundled into the app at build time so
// setup installs a package version that always matches this Studio build.
declare module '*.yaml?raw' {
  const content: string;
  export default content;
}
