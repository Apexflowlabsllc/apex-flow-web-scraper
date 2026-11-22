#!/usr/bin/env node
import { createInterface } from "node:readline/promises";
import { stdin, stdout, stderr } from "node:process";
import { chmodSync, copyFileSync, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { homedir, platform } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import { applyEdits, modify, parse } from "jsonc-parser";
import { startMcpBridge } from "../mcp-bridge.mjs";

export const SERVER_NAME = "apex-flow-web-scraper";
export function getMcpCommand(os = platform()) {
  return os === "win32"
    ? { command: "cmd", args: ["/c", "npx", "-y", "apex-flow-web-scraper", "mcp"] }
    : { command: "npx", args: ["-y", "apex-flow-web-scraper", "mcp"] };
}
export const MCP_COMMAND = getMcpCommand();

export function getClientTargets({ home = homedir(), appData = process.env.APPDATA, xdgConfigHome = process.env.XDG_CONFIG_HOME, os = platform(), cwd = process.cwd() } = {}) {
  const claudeDesktop = os === "win32"
    ? join(appData || join(home, "AppData", "Roaming"), "Claude", "claude_desktop_config.json")
    : os === "darwin"
      ? join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json")
      : join(home, ".config", "Claude", "claude_desktop_config.json");
  const zedSettings = os === "win32"
    ? join(appData || join(home, "AppData", "Roaming"), "Zed", "settings.json")
    : os === "darwin"
      ? join(home, "Library", "Application Support", "Zed", "settings.json")
      : join(xdgConfigHome || join(home, ".config"), "zed", "settings.json");
  const opencodeSettings = join(xdgConfigHome || join(home, ".config"), "opencode", "opencode.json");
  const jetbrainsAirSettings = join(home, ".jetbrains", "mcp", "mcp.json");
  return [
    { id: "codex", label: "OpenAI Codex CLI", format: "toml", path: join(home, ".codex", "config.toml"), detect: join(home, ".codex") },
    { id: "claude-code", label: "Claude Code", format: "jsonc", path: join(home, ".claude.json"), detect: join(home, ".claude"), entryType: "stdio" },
    { id: "claude-desktop", label: "Claude Desktop", format: "jsonc", path: claudeDesktop, detect: dirname(claudeDesktop) },
    { id: "grok", label: "Grok CLI", format: "toml", path: join(home, ".grok", "config.toml"), detect: join(home, ".grok"), remoteUrl: "https://webscraper.apexflowlabs.com/v1/mcp", authEnvVar: "APEX_SCRAPER_API_KEY" },
    { id: "cursor", label: "Cursor", format: "jsonc", path: join(home, ".cursor", "mcp.json"), detect: join(home, ".cursor") },
    { id: "gemini", label: "Gemini CLI", format: "jsonc", path: join(home, ".gemini", "settings.json"), detect: join(home, ".gemini") },
    { id: "windsurf", label: "Windsurf", format: "jsonc", path: join(home, ".codeium", "windsurf", "mcp_config.json"), detect: join(home, ".codeium", "windsurf") },
    { id: "copilot", label: "GitHub Copilot CLI and VS Code", format: "jsonc", path: join(home, ".copilot", "mcp-config.json"), detect: join(home, ".copilot"), entryType: "stdio" },
    { id: "cline", label: "Cline CLI", format: "jsonc", path: join(home, ".cline", "data", "settings", "cline_mcp_settings.json"), detect: join(home, ".cline"), entryType: "stdio" },
    { id: "roo-code", label: "Roo Code (workspace)", format: "jsonc", path: join(cwd, ".roo", "mcp.json"), detect: join(cwd, ".roo"), entryType: "stdio" },
    { id: "continue", label: "Continue (workspace)", format: "jsonc", path: join(cwd, ".continue", "mcpServers", `${SERVER_NAME}.json`), detect: join(cwd, ".continue"), rootKey: "mcpServers" },
    { id: "opencode", label: "OpenCode", format: "jsonc", path: opencodeSettings, detect: dirname(opencodeSettings), rootKey: ["mcp", "servers"], entryFactory: ({ os: targetOs }) => {
      const command = getMcpCommand(targetOs);
      return { type: "local", command: [command.command, ...command.args], environment: { APEX_SCRAPER_API_KEY: "{env:APEX_SCRAPER_API_KEY}" } };
    } },
    { id: "amazon-q", label: "Amazon Q Developer (legacy MCP config)", format: "jsonc", path: join(home, ".aws", "amazonq", "mcp.json"), detect: join(home, ".aws", "amazonq") },
    { id: "jetbrains-air", label: "JetBrains Air", format: "jsonc", path: jetbrainsAirSettings, detect: dirname(jetbrainsAirSettings) },
    { id: "zed", label: "Zed", format: "jsonc", path: zedSettings, detect: dirname(zedSettings), rootKey: "context_servers" },
  ];
}

export function jsoncWithMcpServer(source, { force = false, entry = MCP_COMMAND, rootKey = "mcpServers" } = {}) {
  const text = source.trim() ? source : "{}\n";
  const errors = [];
  const data = parse(text, errors, { allowTrailingComma: true, disallowComments: false });
  if (errors.length) throw new Error("The MCP settings file has JSON errors; no changes were made.");
  if (data !== undefined && (data === null || Array.isArray(data) || typeof data !== "object")) {
    throw new Error("The MCP settings file must contain a JSON object; no changes were made.");
  }
  const rootPath = Array.isArray(rootKey) ? rootKey : [rootKey];
  const existingRoot = rootPath.reduce((value, key) => value?.[key], data);
  const existing = existingRoot?.[SERVER_NAME];
  if (existing && !force) {
    if (JSON.stringify(existing) === JSON.stringify(entry)) return { text, changed: false, reason: "already configured" };
    throw new Error(`An existing ${SERVER_NAME} entry was found; use --force to replace it.`);
  }
  const edits = modify(text, [...rootPath, SERVER_NAME], entry, {
    formattingOptions: { insertSpaces: true, tabSize: 2, eol: text.includes("\r\n") ? "\r\n" : "\n" },
  });
  return { text: applyEdits(text, edits), changed: true };
}

export function tomlWithMcpServer(source, { force = false, os = platform(), remoteUrl, authEnvVar = "APEX_SCRAPER_API_KEY" } = {}) {
  const section = "[mcp_servers.apex_flow_web_scraper]";
  const lines = source.split(/\r?\n/);
  const header = /^\s*\[\[?\s*mcp_servers\s*\.\s*(?:apex_flow_web_scraper|\"apex_flow_web_scraper\"|'apex_flow_web_scraper')\s*\]\]?\s*(?:#.*)?$/;
  const childHeader = /^\s*\[\[?\s*mcp_servers\s*\.\s*(?:apex_flow_web_scraper|\"apex_flow_web_scraper\"|'apex_flow_web_scraper')\s*\.\s*[^\]]+\]\]?\s*(?:#.*)?$/;
  const start = lines.findIndex((line) => header.test(line));
  if (start >= 0 && !force) return { text: source, changed: false, reason: "already configured; review the existing section manually" };
  const command = getMcpCommand(os);
  const block = remoteUrl
    ? [section, `url = ${JSON.stringify(remoteUrl)}`, `headers = { "Authorization" = ${JSON.stringify("Bearer ${" + authEnvVar + "}")} }`]
    : [section, `command = ${JSON.stringify(command.command)}`, `args = ${JSON.stringify(command.args)}`];
  if (start >= 0) {
    let end = start + 1;
    while (end < lines.length) {
      if (header.test(lines[end])) break;
      if (/^\s*\[\[?[^\]]+\]\]?\s*(?:#.*)?$/.test(lines[end]) && !childHeader.test(lines[end])) break;
      end++;
    }
    lines.splice(start, end - start, ...block);
    return { text: lines.join("\n"), changed: true };
  }
  const prefix = source && !source.endsWith("\n") ? "\n" : "";
  return { text: `${source}${prefix}${source ? "\n" : ""}${block.join("\n")}\n`, changed: true };
}

function makeBackup(path) {
  if (!existsSync(path)) return null;
  const stamp = new Date().toISOString().replaceAll(":", "-");
  const backup = `${path}.apex-flow-${stamp}.bak`;
  copyFileSync(path, backup);
  try { chmodSync(backup, statSync(path).mode & 0o777); } catch { /* keep the OS-created backup permissions */ }
  return backup;
}

export function installClient(target, { force = false, dryRun = false, os = platform() } = {}) {
  const oldText = existsSync(target.path) ? readFileSync(target.path, "utf8") : "";
  const result = target.format === "toml"
    ? tomlWithMcpServer(oldText, { force, os, remoteUrl: target.remoteUrl, authEnvVar: target.authEnvVar })
    : jsoncWithMcpServer(oldText, { force, rootKey: target.rootKey, entry: target.entryFactory ? target.entryFactory({ os }) : { ...(target.entryType ? { type: target.entryType } : {}), ...getMcpCommand(os) } });
  if (!result.changed || dryRun) return { ...result, backup: null };
  mkdirSync(dirname(target.path), { recursive: true });
  const backup = makeBackup(target.path);
  const temp = `${target.path}.apex-flow-${process.pid}.tmp`;
  const mode = existsSync(target.path) ? statSync(target.path).mode & 0o777 : 0o600;
  writeFileSync(temp, result.text, { mode });
  renameSync(temp, target.path);
  return { ...result, backup };
}

function printHelp() {
  stdout.write(`Apex Flow Web Scraper ${process.env.npm_package_version || ""}\n\n`);
  stdout.write("Commands:\n  setup [--all | --client <name>] [--yes] [--force] [--dry-run]\n  mcp\n  clients\n\n");
  stdout.write("Clients: codex, claude-code, claude-desktop, grok, cursor, gemini, windsurf, copilot, cline, roo-code, continue, opencode, amazon-q, jetbrains-air, zed\n");
  stdout.write("The MCP bridge reads APEX_SCRAPER_API_KEY from its environment. It never writes the key into client settings.\n");
}

function parseArgs(argv) {
  const options = { command: argv[0] || "help", all: false, client: null, yes: false, force: false, dryRun: false };
  for (let i = 1; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--all") options.all = true;
    else if (arg === "--client") options.client = argv[++i];
    else if (arg === "--yes" || arg === "-y") options.yes = true;
    else if (arg === "--force") options.force = true;
    else if (arg === "--dry-run") options.dryRun = true;
    else if (arg === "--help" || arg === "-h") options.command = "help";
    else throw new Error(`Unknown option: ${arg}`);
  }
  return options;
}

export async function runSetup(options, { home = homedir(), appData = process.env.APPDATA, os = platform(), io = { stdin, stdout, stderr } } = {}) {
  const targets = getClientTargets({ home, appData, os });
  const aliases = { claude: "claude-code", "grok-cli": "grok", vscode: "copilot", "github-copilot": "copilot", roo: "roo-code" };
  let selected;
  if (options.client) {
    const id = aliases[options.client] || options.client;
    const target = targets.find((item) => item.id === id);
    if (!target) throw new Error(`Unsupported client: ${options.client}`);
    selected = [target];
  } else if (options.all) {
    selected = targets;
  } else {
    selected = targets.filter((target) => existsSync(target.detect) || existsSync(target.path));
    if (!selected.length) throw new Error("No supported AI client configuration folders were found. Choose one with --client <name>.");
  }
  if (options.dryRun) {
    for (const target of selected) io.stdout.write(`${target.id}: ${target.path}\n`);
    return { selected: selected.map((target) => target.id), applied: false };
  }
  if (!options.yes) {
    if (!io.stdin.isTTY) throw new Error("This command needs an interactive confirmation. Re-run with --yes after reviewing the selected client paths.");
    const rl = createInterface({ input: io.stdin, output: io.stdout });
    const answer = await rl.question(`Add Apex Flow Web Scraper to ${selected.map((target) => target.label).join(", ")}? [y/N] `);
    rl.close();
    if (!/^y(es)?$/i.test(answer.trim())) return { selected: selected.map((target) => target.id), applied: false };
  }
  const results = selected.map((target) => ({ target, result: installClient(target, { force: options.force, os }) }));
  for (const { target, result } of results) {
    if (result.changed) io.stdout.write(`Configured ${target.label}: ${target.path}${result.backup ? ` (backup: ${result.backup})` : ""}\n`);
    else io.stdout.write(`${target.label}: ${result.reason}\n`);
  }
  if (!process.env.APEX_SCRAPER_API_KEY) io.stdout.write("Next: set APEX_SCRAPER_API_KEY in the environment inherited by your AI clients, then restart them.\n");
  io.stdout.write("The API key was not saved in any client configuration.\n");
  return { selected: selected.map((target) => target.id), applied: true };
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  if (options.command === "help") return printHelp();
  if (options.command === "clients") {
    for (const target of getClientTargets()) stdout.write(`${target.id}\t${target.label}\t${target.path}\n`);
    return;
  }
  if (options.command === "setup") return runSetup(options);
  if (options.command === "mcp") return startMcpBridge();
  throw new Error(`Unknown command: ${options.command}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    stderr.write(`Apex Flow Web Scraper: ${error.message}\n`);
    process.exitCode = 1;
  });
}
