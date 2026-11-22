import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { getClientTargets, getMcpCommand, installClient, jsoncWithMcpServer, MCP_COMMAND, SERVER_NAME, tomlWithMcpServer } from "./bin/apex-flow-web-scraper.mjs";

const scratchBase = resolve(process.cwd(), "work", "installer-tests");
mkdirSync(scratchBase, { recursive: true });
const scratch = [];
function isolatedHome() {
  const path = mkdtempSync(join(scratchBase, "home-"));
  const rel = relative(scratchBase, path);
  assert.ok(rel && !rel.startsWith(`..${sep}`) && rel !== "..", "test home must remain within the E: project scratch folder");
  scratch.push(path);
  return path;
}
test.after(() => {
  for (const path of scratch) {
    const rel = relative(scratchBase, path);
    if (rel && !rel.startsWith(`..${sep}`) && rel !== "..") rmSync(path, { recursive: true, force: true });
  }
});

test("JSONC integration preserves comments and unrelated MCP servers", () => {
  const source = '{\n  // keep this comment\n  "mcpServers": {"existing": {"command": "existing"}}\n}\n';
  const result = jsoncWithMcpServer(source);
  assert.equal(result.changed, true);
  assert.match(result.text, /keep this comment/);
  const value = JSON.parse(result.text.replace(/\/\/.*$/gm, ""));
  assert.deepEqual(value.mcpServers.existing, { command: "existing" });
  assert.deepEqual(value.mcpServers[SERVER_NAME], MCP_COMMAND);
  assert.doesNotMatch(JSON.stringify(value), /API_KEY|Bearer/);
});

test("JSONC integration is idempotent and refuses to overwrite a conflicting entry", () => {
  const once = jsoncWithMcpServer("{}").text;
  assert.equal(jsoncWithMcpServer(once).changed, false);
  assert.throws(() => jsoncWithMcpServer('{"mcpServers":{"apex-flow-web-scraper":{"command":"custom"}}}'), /--force/);
  assert.deepEqual(JSON.parse(jsoncWithMcpServer('{"mcpServers":{"apex-flow-web-scraper":{"command":"custom"}}}', { force: true }).text).mcpServers[SERVER_NAME], MCP_COMMAND);
});

test("Zed context_servers format keeps unrelated settings and never embeds a key", () => {
  const result = jsoncWithMcpServer('{\n  "theme": "One Dark",\n  "context_servers": {"other": {"command":"node"}}\n}', {
    rootKey: "context_servers", entry: { command: "npx", args: ["-y", "apex-flow-web-scraper", "mcp"], env: {} },
  });
  const value = JSON.parse(result.text);
  assert.equal(value.theme, "One Dark");
  assert.deepEqual(value.context_servers.other, { command: "node" });
  assert.deepEqual(value.context_servers[SERVER_NAME], { command: "npx", args: ["-y", "apex-flow-web-scraper", "mcp"], env: {} });
  assert.doesNotMatch(JSON.stringify(value), /API_KEY|Bearer/);
});

test("target registry includes individually selectable Cline, Roo Code and Zed installs", () => {
  const home = "E:/fixture-home";
  const cwd = "E:/fixture-project";
  const targets = getClientTargets({ home, cwd, appData: "E:/fixture-appdata", xdgConfigHome: "E:/fixture-config", os: "win32" });
  const byId = Object.fromEntries(targets.map((target) => [target.id, target]));
  assert.equal(byId.cline.path, join(home, ".cline", "data", "settings", "cline_mcp_settings.json"));
  assert.equal(byId["roo-code"].path, join(cwd, ".roo", "mcp.json"));
  assert.equal(byId.zed.path, join("E:/fixture-appdata", "Zed", "settings.json"));
  assert.equal(byId.zed.rootKey, "context_servers");
});

test("Codex TOML integration appends a stdio server and does not duplicate it", () => {
  const first = tomlWithMcpServer("model = \"gpt-test\"\n", { os: "linux" });
  assert.match(first.text, /\[mcp_servers\.apex_flow_web_scraper\]/);
  assert.match(first.text, /args = \[\"-y\",\"apex-flow-web-scraper\",\"mcp\"\]/);
  assert.equal(tomlWithMcpServer(first.text, { os: "linux" }).changed, false);
});

test("Codex TOML integration recognizes quoted and array-table entries", () => {
  const quoted = '[mcp_servers."apex_flow_web_scraper"]\ncommand = "custom"\n\n[unrelated]\nkeep = true\n';
  assert.equal(tomlWithMcpServer(quoted).changed, false);
  const arrayTable = '[[mcp_servers.apex_flow_web_scraper]]\ncommand = "custom"\n';
  assert.equal(tomlWithMcpServer(arrayTable).changed, false);
  const replaced = tomlWithMcpServer('[mcp_servers."apex_flow_web_scraper"]\ncommand = "custom"\n[mcp_servers.apex_flow_web_scraper.options]\nfoo = true\n\n[unrelated]\nkeep = true\n', { force: true, os: "linux" });
  assert.match(replaced.text, /\[unrelated\]/);
  assert.doesNotMatch(replaced.text, /apex_flow_web_scraper\.options/);
});

test("Windows launchers use cmd.exe for npm's npx shim", () => {
  assert.deepEqual(getMcpCommand("win32"), { command: "cmd", args: ["/c", "npx", "-y", "apex-flow-web-scraper", "mcp"] });
  assert.deepEqual(getMcpCommand("linux"), { command: "npx", args: ["-y", "apex-flow-web-scraper", "mcp"] });
});

test("installer writes an isolated client config with a restrictive mode and a backup", () => {
  const home = isolatedHome();
  const dir = join(home, ".cursor");
  mkdirSync(dir);
  const file = join(dir, "mcp.json");
  writeFileSync(file, '{\n  "mcpServers": {"other": {"command":"node"}}\n}\n', { mode: 0o600 });
  const target = { id: "cursor", label: "Cursor", format: "jsonc", path: file };
  const result = installClient(target);
  assert.equal(result.changed, true);
  assert.ok(result.backup && existsSync(result.backup));
  const config = JSON.parse(readFileSync(file, "utf8"));
  assert.deepEqual(config.mcpServers.other, { command: "node" });
  assert.deepEqual(config.mcpServers[SERVER_NAME], MCP_COMMAND);
  if (process.platform !== "win32") assert.equal(statSync(file).mode & 0o777, 0o600);
});

test("installer dry-run does not create config files", () => {
  const home = isolatedHome();
  const file = join(home, ".gemini", "settings.json");
  const result = installClient({ id: "gemini", label: "Gemini CLI", format: "jsonc", path: file }, { dryRun: true });
  assert.equal(result.changed, true);
  assert.equal(existsSync(file), false);
});
