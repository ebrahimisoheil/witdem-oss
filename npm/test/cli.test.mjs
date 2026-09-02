import assert from "node:assert/strict";
import test from "node:test";

import { DEFAULT_IMAGE, VERSION, composeArgs, composeEnvironment, parseArgs } from "../src/cli.mjs";

test("the default image is pinned to the npm package version", () => {
  assert.equal(DEFAULT_IMAGE, `ghcr.io/ebrahimisoheil/witdem-analytics:${VERSION}`);
});

test("up has safe local defaults", () => {
  const options = parseArgs(["up"]);
  assert.equal(options.command, "up");
  assert.equal(options.dashboardPort, "8501");
  assert.equal(options.receiverPort, "4318");
  assert.equal(options.open, true);
});

test("ports, image, and browser behavior are configurable", () => {
  const options = parseArgs([
    "up",
    "--dashboard-port",
    "18501",
    "--receiver-port",
    "14318",
    "--image",
    "example.test/witdem:dev",
    "--no-open",
    "--data-dir",
    "/tmp/witdem-test",
  ]);
  assert.deepEqual(
    {
      dashboardPort: options.dashboardPort,
      receiverPort: options.receiverPort,
      image: options.image,
      open: options.open,
      dataDir: options.dataDir,
    },
    {
      dashboardPort: "18501",
      receiverPort: "14318",
      image: "example.test/witdem:dev",
      open: false,
      dataDir: "/tmp/witdem-test",
    },
  );
});

test("invalid and missing options fail before Docker is called", () => {
  assert.throws(() => parseArgs(["up", "--dashboard-port", "70000"]), /between 1 and 65535/);
  assert.throws(() => parseArgs(["up", "--image"]), /requires a container reference/);
  assert.throws(() => parseArgs(["up", "--surprise"]), /unknown option/);
});

test("compose receives explicit immutable launcher settings", () => {
  const options = parseArgs(["status", "--dashboard-port", "18501"]);
  const env = composeEnvironment(options, { WITDEM_API_KEY: "secret" });
  assert.equal(env.WITDEM_IMAGE, DEFAULT_IMAGE);
  assert.equal(env.WITDEM_DASHBOARD_PORT, "18501");
  assert.equal(env.WITDEM_RECEIVER_PORT, "4318");
  assert.equal(env.WITDEM_DATA_TYPE, "volume");
  assert.equal(env.WITDEM_DATA_SOURCE, "witdem-data");
  assert.equal(env.WITDEM_API_KEY, "secret");
  assert.deepEqual(composeArgs(["ps"]), [
    "compose",
    "--project-name",
    "witdem",
    "--file",
    composeArgs([])[4],
    "ps",
  ]);
});
