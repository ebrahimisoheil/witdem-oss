import { spawn, spawnSync } from "node:child_process";
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
  witdem down [options]     Stop services; collected data is preserved

Options:
  --dashboard-port <port>   Dashboard port (default: 8501)
  --receiver-port <port>    OTLP/SDK receiver port (default: 4318)
  --image <reference>       Override the pinned container image
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
    open: true,
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
    } else if (flag === "--no-open") options.open = false;
    else if (flag === "-h" || flag === "--help") options.command = "help";
    else if (flag === "-v" || flag === "--version") options.command = "version";
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
  };
}

export function composeArgs(args) {
  return ["compose", "--project-name", "witdem", "--file", composeFile, ...args];
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
  return run("docker", composeArgs(args), {
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
    throw error;
  }
  console.log(`\nWitdem is ready.\nDashboard: ${endpoint.dashboard}\nReceiver:  ${endpoint.receiver}`);
  if (options.open) openBrowser(endpoint.dashboard);
}

function dev(options) {
  requireDocker();
  docker(options, ["up", "--remove-orphans"]);
}

async function status(options) {
  requireDocker();
  docker(options, ["ps"]);
  const endpoint = urls(options);
  console.log(`Receiver:  ${(await healthy(`${endpoint.receiver}/readiness`)) ? "healthy" : "not ready"}`);
  console.log(`Dashboard: ${(await healthy(`${endpoint.dashboard}/health`)) ? "healthy" : "not ready"}`);
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
    return console.log("Witdem stopped. Collected data is preserved in the witdem-data volume.");
  }
  if (options.command === "logs") {
    requireDocker();
    return docker(options, ["logs", "--follow"]);
  }
  if (options.command === "open") {
    openBrowser(urls(options).dashboard);
    return;
  }
  throw new Error(`unknown command: ${options.command}`);
}
