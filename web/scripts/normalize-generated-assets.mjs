import { readdir, readFile, writeFile } from "node:fs/promises";
import { extname, resolve } from "node:path";

const output = resolve(import.meta.dirname, "../../src/witdem/dashboard/static");
const supported = new Set([".css", ".html", ".js"]);

async function files(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  return (
    await Promise.all(
      entries.map((entry) => {
        const path = resolve(directory, entry.name);
        return entry.isDirectory() ? files(path) : [path];
      }),
    )
  ).flat();
}

for (const path of await files(output)) {
  if (!supported.has(extname(path))) continue;
  const source = await readFile(path, "utf8");
  const normalized = source.replace(/^[\t ]+$/gm, "");
  if (normalized !== source) await writeFile(path, normalized, "utf8");
}
