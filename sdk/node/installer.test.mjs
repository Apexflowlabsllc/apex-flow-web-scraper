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

test("nested JSONC paths preserve unrelated OpenCode config and use an environment reference", () => {
  const source = '{\n  "theme": "dark",\n  "mcp": {"servers": {"existing": {"type":"local","command":["node"]}}}\n}\n';
  const entry = { type: "local", command: ["npx", "-y", "apex-flow-web-scraper", "mcp"], environment: { APEX_SCRAPER_API_KEY: "{env:APEX_SCRAPER_API_KEY}" } };
  const result = jsoncWithMcpServer(source, { rootKey: ["mcp", "servers"], entry });
  const value = JSON.parse(result.text);
  assert.equal(value.theme, "dark");
  assert.deepEqual(value.mcp.servers.existing, { type: "local", command: ["node"] });
  assert.deepEqual(value.mcp.servers[SERVER_NAME], entry);
  assert.doesNotMatch(JSON.stringify(value), /Bearer |rs_live_/);
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

test("Kiro remote MCP setup preserves existing servers and uses an approved environment reference", () => {
  const target = getClientTargets({ home: "E:/fixture-home", os: "win32" }).find((item) => item.id === "kiro");
  const source = '{\n  "theme": "dark",\n  "mcpServers": {"existing": {"url": "https://example.com/mcp"}}\n}\n';
  const result = jsoncWithMcpServer(source, { rootKey: target.rootKey, entry: target.entryFactory() });
  const value = JSON.parse(result.text);
  assert.equal(value.theme, "dark");
  assert.deepEqual(value.mcpServers.existing, { url: "https://example.com/mcp" });
  assert.deepEqual(value.mcpServers[SERVER_NAME], {
    url: "https://webscraper.apexflowlabs.com/v1/mcp",
    headers: { Authorization: "Bearer ${APEX_SCRAPER_API_KEY}" },
  });
  assert.doesNotMatch(JSON.stringify(value), /rs_live_|your-api-key/i);
});

test("Kiro source setup uses local stdio and environment references without embedding credentials", () => {
  const target = getClientTargets({ home: "E:/fixture-home", os: "win32" }).find((item) => item.id === "kiro");
  const value = target.entryFactory({ os: "win32", source: true });
  assert.equal(value.command, process.execPath);
  assert.equal(value.args.length, 1);
  assert.ok(existsSync(value.args[0]));
  assert.deepEqual(value.env, {
    APEX_SCRAPER_API_KEY: "${APEX_SCRAPER_API_KEY}",
    BRAVE_SEARCH_API_KEY: "${BRAVE_SEARCH_API_KEY}",
    TAVILY_API_KEY: "${TAVILY_API_KEY}",
    EXA_API_KEY: "${EXA_API_KEY}",
    APEX_SEARCH_PROVIDER: "${APEX_SEARCH_PROVIDER}",
  });
  assert.doesNotMatch(JSON.stringify(value), /Bearer |rs_live_|your-api-key/i);
});

test("target registry includes individually selectable Cline, Roo Code, Continue, OpenCode, Amazon Q, JetBrains Air, Zed and Kiro installs", () => {
  const home = "E:/fixture-home";
  const cwd = "E:/fixture-project";
  const targets = getClientTargets({ home, cwd, appData: "E:/fixture-appdata", xdgConfigHome: "E:/fixture-config", os: "win32" });
  const byId = Object.fromEntries(targets.map((target) => [target.id, target]));
  assert.equal(byId.cline.path, join(home, ".cline", "data", "settings", "cline_mcp_settings.json"));
  assert.equal(byId["roo-code"].path, join(cwd, ".roo", "mcp.json"));
  assert.equal(byId.continue.path, join(cwd, ".continue", "mcpServers", `${SERVER_NAME}.json`));
  assert.equal(byId.continue.rootKey, "mcpServers");
  assert.equal(byId.opencode.path, join("E:/fixture-config", "opencode", "opencode.json"));
  assert.deepEqual(byId.opencode.rootKey, ["mcp", "servers"]);
  assert.equal(byId["amazon-q"].path, join(home, ".aws", "amazonq", "mcp.json"));
  assert.equal(byId["jetbrains-air"].path, join(home, ".jetbrains", "mcp", "mcp.json"));
  assert.equal(byId.zed.path, join("E:/fixture-appdata", "Zed", "settings.json"));
  assert.equal(byId.zed.rootKey, "context_servers");
  assert.equal(byId.kiro.path, join(home, ".kiro", "settings", "mcp.json"));
  assert.equal(byId.kiro.rootKey, "mcpServers");
  assert.deepEqual(byId.kiro.entryFactory(), {
    url: "https://webscraper.apexflowlabs.com/v1/mcp",
    headers: { Authorization: "Bearer ${APEX_SCRAPER_API_KEY}" },
  });
  assert.equal(byId.grok.path, join(home, ".grok", "config.toml"));
  assert.equal(byId.grok.remoteUrl, "https://webscraper.apexflowlabs.com/v1/mcp");
  assert.equal(byId.grok.authEnvVar, "APEX_SCRAPER_API_KEY");
});

test("OpenCode target builds its native local-server command and secret reference", () => {
  const home = "E:/fixture-home";
  const target = getClientTargets({ home, xdgConfigHome: "E:/fixture-config", os: "win32" }).find((item) => item.id === "opencode");
  assert.deepEqual(target.entryFactory({ os: "win32" }), {
    type: "local",
    command: ["cmd", "/c", "npx", "-y", "apex-flow-web-scraper", "mcp"],
    environment: {
      APEX_SCRAPER_API_KEY: "{env:APEX_SCRAPER_API_KEY}",
      BRAVE_SEARCH_API_KEY: "{env:BRAVE_SEARCH_API_KEY}",
      TAVILY_API_KEY: "{env:TAVILY_API_KEY}",
      EXA_API_KEY: "{env:EXA_API_KEY}",
      APEX_SEARCH_PROVIDER: "{env:APEX_SEARCH_PROVIDER}",
    },
  });
});

test("source MCP command starts the checked-out bridge with the current Node executable", () => {
  const command = getMcpCommand("win32", { source: true });
  assert.equal(command.command, process.execPath);
  assert.equal(command.args.length, 1);
  assert.ok(existsSync(command.args[0]));
  assert.match(command.args[0], /mcp-bridge\.mjs$/);
  assert.doesNotMatch(JSON.stringify(command), /npx|apex-flow-web-scraper mcp/);
});

test("Grok source setup writes a local stdio server with environment references", () => {
  const result = tomlWithMcpServer("", { os: "win32", sourceBridge: true });
  assert.ok(result.text.includes(`command = ${JSON.stringify(process.execPath)}`));
  assert.match(result.text, /mcp-bridge\.mjs/);
  assert.ok(result.text.includes('BRAVE_SEARCH_API_KEY = "${BRAVE_SEARCH_API_KEY}"'));
  assert.doesNotMatch(result.text, /npx|rs_live_|your-api-key/i);
});

test("Codex TOML integration appends a stdio server and does not duplicate it", () => {
  const first = tomlWithMcpServer("model = \"gpt-test\"\n", { os: "linux" });
  assert.match(first.text, /\[mcp_servers\.apex_flow_web_scraper\]/);
  assert.match(first.text, /args = \[\"-y\",\"apex-flow-web-scraper\",\"mcp\"\]/);
  assert.equal(tomlWithMcpServer(first.text, { os: "linux" }).changed, false);
});

test("Grok CLI config uses remote MCP and an environment reference instead of storing the API key", () => {
  const target = getClientTargets({ home: "E:/fixture-home", os: "win32" }).find((item) => item.id === "grok");
  const result = tomlWithMcpServer("", {
    remoteUrl: target.remoteUrl,
    authEnvVar: target.authEnvVar,
  });
  assert.equal(result.text, '[mcp_servers.apex_flow_web_scraper]\nurl = "https://webscraper.apexflowlabs.com/v1/mcp"\nheaders = { "Authorization" = "Bearer ${APEX_SCRAPER_API_KEY}" }\n');
  assert.doesNotMatch(result.text, /rs_live_/);
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

test("source installer points clients at the checkout without saving API keys", () => {
  const home = isolatedHome();
  const file = join(home, ".cursor", "mcp.json");
  const target = { id: "cursor", label: "Cursor", format: "jsonc", path: file };
  const result = installClient(target, { source: true });
  assert.equal(result.changed, true);
  const server = JSON.parse(readFileSync(file, "utf8")).mcpServers[SERVER_NAME];
  assert.equal(server.command, process.execPath);
  assert.equal(server.args.length, 1);
  assert.ok(existsSync(server.args[0]));
  assert.doesNotMatch(JSON.stringify(server), /API_KEY|Bearer |rs_live_|your-api-key/i);
});
