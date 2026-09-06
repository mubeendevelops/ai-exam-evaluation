// frontend/scripts/generate-api.mjs — regenerates src/api/schema.d.ts from
// the FastAPI app's OpenAPI schema.
//
// Two steps, chained so one `npm run generate:api` does both:
//   1. Run the backend's Python env against scripts/export_openapi_schema.py,
//      which imports api/main.py directly (no server, no DB) and prints the
//      OpenAPI document as JSON.
//   2. Pipe that into openapi-typescript to (re)write src/api/schema.d.ts.
//
// Whenever anything under api/schemas/ (or any route signature) changes,
// re-run `npm run generate:api` — the generated file is not hand-edited and
// drifts silently otherwise.
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..");
const frontendRoot = path.resolve(here, "..");

function findPython() {
  if (process.env.PYTHON) return process.env.PYTHON;
  const venvPython = path.join(repoRoot, ".venv-paddleocr", "bin", "python");
  if (existsSync(venvPython)) return venvPython;
  return "python3";
}

const python = findPython();
const exportScript = path.join(here, "export_openapi_schema.py");

const schemaResult = spawnSync(python, [exportScript], {
  cwd: repoRoot,
  encoding: "utf-8",
  maxBuffer: 1024 * 1024 * 64,
});

if (schemaResult.status !== 0) {
  process.stderr.write(schemaResult.stderr ?? "");
  console.error(
    `\nFailed to export the OpenAPI schema using ${python}. If this is not ` +
      `the backend's virtualenv, set PYTHON=/path/to/venv/bin/python and retry.`,
  );
  process.exit(schemaResult.status ?? 1);
}

const outFile = path.join(frontendRoot, "src", "api", "schema.d.ts");

// openapi-typescript's resolver reads its input as a real file path (piping
// via /dev/stdin isn't reliable across environments), so stage the JSON in a
// scratch file rather than shelling stdin straight through.
const scratchDir = mkdtempSync(path.join(tmpdir(), "openapi-schema-"));
const scratchFile = path.join(scratchDir, "schema.json");
writeFileSync(scratchFile, schemaResult.stdout);

let genResult;
try {
  genResult = spawnSync(
    "npx",
    ["openapi-typescript", scratchFile, "-o", outFile],
    { cwd: frontendRoot, stdio: "inherit" },
  );
} finally {
  rmSync(scratchDir, { recursive: true, force: true });
}

process.exit(genResult.status ?? 1);
