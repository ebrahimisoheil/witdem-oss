import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const composeFile = path.join(packageRoot, "compose.yaml");
const packageJson = JSON.parse(await readFile(path.join(packageRoot, "package.json"), "utf8"));

export const VERSION = packageJson.version;
export const DEFAULT_IMAGE = `ghcr.io/ebrahimisoheil/witdem-analytics:${VERSION}`;

const HELP = `Witdem ${VERSION}

Run the complete local analytics environment with Docker.

Usage:
  witdem up [options]       Start in the background and open the dashboard
  witdem dev [options]      Start in the foreground and stream logs
  witdem open [options]     Open the dashboard
  witdem status [options]   Show service and endpoint health
  witdem logs [options]     Follow service logs
  witdem doctor [options]   Check local prerequisites
  witdem version            Show the package version
  witdem update --check     Check and print upgrade guidance
  witdem down [options]     Stop services; collected data is preserved
  witdem workflow compile   Compile configured workflow YAML
  witdem workflow rebuild   Rebuild materialized workflow projections

Options:
  --dashboard-port <port>   Dashboard port (default: 8501)
  --receiver-port <port>    OTLP/SDK receiver port (default: 4318)
  --image <reference>       Override the pinned container image
  --data-dir <path>         Use an explicit persistent host directory
  --project-name <name>     Isolate this Compose installation
  --follow                  Continue streaming logs
  --json                    Emit machine-readable output when supported
  --open                    Open the browser after "up" (default)
  --no-open                 Do not open the browser after "up"
  -h, --help                Show this help
  -v, --version             Show the package version

Environment:
  WITDEM_API_KEY            Optional receiver bearer key
  WITDEM_IMAGE              Container override (normally unnecessary)
`;

function port(value, flag) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
    throw new Error(`${flag} must be an integer between 1 and 65535`);
  }
  return String(parsed);
}

export function parseArgs(argv) {
  const options = {
    command: "help",
    dashboardPort: process.env.WITDEM_DASHBOARD_PORT || "8501",
    receiverPort: process.env.WITDEM_RECEIVER_PORT || "4318",
    image: process.env.WITDEM_IMAGE || DEFAULT_IMAGE,
    dataDir: process.env.WITDEM_DATA_DIR || null,
    projectName: process.env.WITDEM_PROJECT_NAME || "witdem",
    open: true,
    follow: false,
    json: false,
    check: false,
    refresh: false,
    offline: false,
    force: false,
    positionals: [],
  };
  const args = [...argv];
  if (args[0] && !args[0].startsWith("-")) options.command = args.shift();
  while (args.length) {
    const flag = args.shift();
    if (flag === "--dashboard-port") options.dashboardPort = port(args.shift(), flag);
    else if (flag === "--receiver-port") options.receiverPort = port(args.shift(), flag);
    else if (flag === "--image") {
      options.image = args.shift();
      if (!options.image) throw new Error("--image requires a container reference");
    } else if (flag === "--data-dir") {
      const value = args.shift();
      if (!value) throw new Error("--data-dir requires a path");
      options.dataDir = path.resolve(value);
      if (options.projectName === "witdem") {
        const suffix = createHash("sha256").update(options.dataDir).digest("hex").slice(0, 10);
        options.projectName = `witdem-${suffix}`;
      }
    } else if (flag === "--project-name") {
      options.projectName = args.shift();
      if (!options.projectName || !/^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(options.projectName)) {
        throw new Error("--project-name requires a Compose-safe name");
      }
    } else if (flag === "--open") options.open = true;
    else if (flag === "--no-open") options.open = false;
    else if (flag === "--follow") options.follow = true;
    else if (flag === "--json") options.json = true;
    else if (flag === "--check") options.check = true;
    else if (flag === "--refresh") options.refresh = true;
    else if (flag === "--offline") options.offline = true;
    else if (flag === "--force") options.force = true;
    else if (flag === "-h" || flag === "--help") options.command = "help";
    else if (flag === "-v" || flag === "--version") options.command = "version";
    else if (flag && !flag.startsWith("-")) options.positionals.push(flag);
    else throw new Error(`unknown option: ${flag}`);
  }
  options.dashboardPort = port(options.dashboardPort, "dashboard port");
  options.receiverPort = port(options.receiverPort, "receiver port");
  return options;
}

export function composeEnvironment(options, base = process.env) {
  return {
    ...base,
    WITDEM_IMAGE: options.image,
    WITDEM_DASHBOARD_PORT: options.dashboardPort,
    WITDEM_RECEIVER_PORT: options.receiverPort,
    WITDEM_DATA_TYPE: options.dataDir ? "bind" : "volume",
    WITDEM_DATA_SOURCE: options.dataDir || "witdem-data",
    WITDEM_UPDATE_CHECK: base.WITDEM_UPDATE_CHECK || "1",
  };
}

export function composeArgs(args, options = { projectName: "witdem" }) {
  return ["compose", "--project-name", options.projectName, "--file", composeFile, ...args];
}

function run(command, args, { env = process.env, capture = false } = {}) {
  const result = spawnSync(command, args, {
    env,
    encoding: "utf8",
    stdio: capture ? "pipe" : "inherit",
  });
  if (result.error) throw new Error(`${command} could not be started: ${result.error.message}`);
  if (result.signal === "SIGINT" || result.signal === "SIGTERM") return "";
  if (result.status !== 0) {
    const detail = capture ? (result.stderr || result.stdout || "").trim() : "";
    throw new Error(`${command} ${args.join(" ")} failed${detail ? `: ${detail}` : ""}`);
  }
  return result.stdout?.trim() || "";
}

function docker(options, args, config = {}) {
  return run("docker", composeArgs(args, options), {
    env: composeEnvironment(options),
    ...config,
  });
}

function requireDocker() {
  run("docker", ["--version"], { capture: true });
  run("docker", ["compose", "version"], { capture: true });
  run("docker", ["info"], { capture: true });
}

async function healthy(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(2000) });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitFor(url, label, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await healthy(url)) return;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`${label} did not become healthy at ${url}`);
}

function urls(options) {
  return {
    dashboard: `http://127.0.0.1:${options.dashboardPort}`,
    receiver: `http://127.0.0.1:${options.receiverPort}`,
  };
}

function openBrowser(url) {
  const command = process.platform === "darwin" ? "open" : process.platform === "win32" ? "cmd" : "xdg-open";
  const args = process.platform === "win32" ? ["/c", "start", "", url] : [url];
  const child = spawn(command, args, { detached: true, stdio: "ignore" });
  child.on("error", () => {
    console.log(`Open ${url} in your browser.`);
  });
  child.unref();
}

async function up(options) {
  requireDocker();
  const endpoint = urls(options);
  console.log(`Starting Witdem ${VERSION}…`);
  docker(options, ["up", "--detach", "--remove-orphans"]);
  try {
    await Promise.all([
      waitFor(`${endpoint.receiver}/readiness`, "receiver"),
      waitFor(`${endpoint.dashboard}/health`, "dashboard"),
    ]);
  } catch (error) {
    docker(options, ["ps"]);
    console.error("\nRecent service logs:");
    docker(options, ["logs", "--tail", "80"]);
    docker(options, ["down", "--remove-orphans"]);
    throw error;
  }
  const result = { status: "ready", dashboard: endpoint.dashboard, receiver: endpoint.receiver };
  if (options.json) console.log(JSON.stringify(result, null, 2));
  else console.log(`\nWitdem is ready.\nDashboard: ${endpoint.dashboard}\nReceiver:  ${endpoint.receiver}`);
  if (options.open) openBrowser(endpoint.dashboard);
}

function dev(options) {
  requireDocker();
  docker(options, ["up", "--remove-orphans"]);
}

async function status(options) {
  requireDocker();
  const endpoint = urls(options);
  const result = {
    receiver: { healthy: await healthy(`${endpoint.receiver}/readiness`) },
    dashboard: { healthy: await healthy(`${endpoint.dashboard}/health`) },
  };
  if (options.json) console.log(JSON.stringify({ version: VERSION, services: result }, null, 2));
  else {
    docker(options, ["ps"]);
    console.log(`Receiver:  ${result.receiver.healthy ? "healthy" : "not ready"}`);
    console.log(`Dashboard: ${result.dashboard.healthy ? "healthy" : "not ready"}`);
  }
  if (!result.receiver.healthy || !result.dashboard.healthy) {
    throw new Error("one or more Witdem services are unhealthy");
  }
}

function maintenance(options, command, args = []) {
  requireDocker();
  return docker(options, ["run", "--rm", "receiver", "witdem", command, ...args]);
}

async function doctor(options) {
  console.log(`Node:           ${process.version}`);
  const dockerVersion = run("docker", ["--version"], { capture: true });
  const composeVersion = run("docker", ["compose", "version"], { capture: true });
  run("docker", ["info"], { capture: true });
  console.log(`Docker:         ${dockerVersion}`);
  console.log(`Docker Compose: ${composeVersion}`);
  console.log(`Image:          ${options.image}`);
  const endpoint = urls(options);
  console.log(`Receiver:       ${(await healthy(`${endpoint.receiver}/readiness`)) ? "running" : "available after start"}`);
  console.log(`Dashboard:      ${(await healthy(`${endpoint.dashboard}/health`)) ? "running" : "available after start"}`);
  console.log("\nWitdem can run on this machine.");
}

export async function main(argv) {
  const options = parseArgs(argv);
  if (options.command === "help") return console.log(HELP);
  if (options.command === "version") return console.log(VERSION);
  if (options.command === "up") return up(options);
  if (options.command === "dev") return dev(options);
  if (options.command === "status") return status(options);
  if (options.command === "doctor") return doctor(options);
  if (options.command === "down") {
    requireDocker();
    docker(options, ["down", "--remove-orphans"]);
    return console.log(`Witdem stopped. Collected data is preserved in ${options.dataDir || `${options.projectName}_witdem-data`}.`);
  }
  if (options.command === "logs") {
    requireDocker();
    const service = options.positionals[0];
    if (service && !["receiver", "worker", "dashboard"].includes(service)) {
      throw new Error(`unknown service: ${service}`);
    }
    return docker(options, ["logs", options.follow ? "--follow" : "--tail", options.follow ? undefined : "200", service].filter(Boolean));
  }
  if (options.command === "open") {
    openBrowser(urls(options).dashboard);
    return;
  }
  if (options.command === "update") {
    if (!options.check) throw new Error("update is detection-only; use 'witdem update --check'");
    const flags = ["--check", "--data-dir", "/app/data"];
    if (options.refresh) flags.push("--refresh");
    if (options.offline) flags.push("--offline");
    if (options.json) flags.push("--json");
    return maintenance(options, "update", flags);
  }
  if (options.command === "workflow") {
    const action = options.positionals[0];
    if (action === "compile") {
      return maintenance(options, "workflow", [
        "compile",
        "--data-dir",
        "/app/data",
        ...(options.check ? ["--check"] : []),
        ...(options.force ? ["--force"] : []),
      ]);
    }
    if (action === "rebuild") return maintenance(options, "workflow", ["rebuild", "--data-dir", "/app/data"]);
    throw new Error("workflow requires 'compile' or 'rebuild'");
  }
  throw new Error(`unknown command: ${options.command}`);
}
