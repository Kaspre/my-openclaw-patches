#!/usr/bin/env node
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createServer } from "node:http";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import readline from "node:readline";

const home = process.env.HOME;
const openclawRoot =
  process.env.OPENCLAW_PACKAGE_ROOT || `${home}/.local/node-current/lib/node_modules/openclaw`;
const openclawMjs = process.env.OPENCLAW_MJS || `${openclawRoot}/openclaw.mjs`;
const openclawNode = process.env.OPENCLAW_NODE || process.execPath;
const codexBin =
  process.env.CODEX_BIN ||
  findCodexBinary(
    `${home}/.openclaw/npm/projects`,
    "node_modules/@openclaw/codex/node_modules/@openai/codex-linux-x64/vendor",
  );
const statusMessage = "OpenClaw native hook relay";

function findCodexBinary(projectsRoot, vendorSuffix) {
  try {
    for (const project of readdirSync(projectsRoot)) {
      if (!project.startsWith("openclaw-codex-")) continue;
      const vendorRoot = join(projectsRoot, project, vendorSuffix);
      for (const vendor of readdirSync(vendorRoot)) {
        const candidate = join(vendorRoot, vendor, "bin/codex");
        if (existsSync(candidate)) return candidate;
      }
    }
  } catch {
    // Fall through to the explicit error below.
  }
  throw new Error("Unable to locate installed @openclaw/codex bundled codex binary");
}

function sse(events) {
  return events
    .map((event) => {
      const data = Object.keys(event).length === 1 ? "" : `data: ${JSON.stringify(event)}\n`;
      return `event: ${event.type}\n${data}\n`;
    })
    .join("");
}

function evResponseCreated(id) {
  return { type: "response.created", response: { id } };
}

function evCompleted(id) {
  return {
    type: "response.completed",
    response: {
      id,
      usage: {
        input_tokens: 0,
        input_tokens_details: null,
        output_tokens: 0,
        output_tokens_details: null,
        total_tokens: 0,
      },
    },
  };
}

function evCustomToolCall(callId, name, input) {
  return {
    type: "response.output_item.done",
    item: { type: "custom_tool_call", call_id: callId, name, input },
  };
}

function evAssistantMessage(id, text) {
  return {
    type: "response.output_item.done",
    item: {
      type: "message",
      role: "assistant",
      id,
      content: [{ type: "output_text", text }],
    },
  };
}

async function startMockResponsesServer(source) {
  const requests = [];
  const responses = [
    sse([
      evResponseCreated("resp-1"),
      evCustomToolCall("call-code-mode-valid-relay-deny", "exec", source),
      evCompleted("resp-1"),
    ]),
    sse([evResponseCreated("resp-2"), evAssistantMessage("msg-1", "done"), evCompleted("resp-2")]),
  ];
  const server = createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      requests.push({
        method: req.method,
        url: req.url,
        body: Buffer.concat(chunks).toString("utf8"),
      });
      if (req.method !== "POST" || !req.url?.endsWith("/responses")) {
        res.writeHead(404);
        res.end("not found");
        return;
      }
      const body = responses.shift() ?? responses.at(-1) ?? "";
      res.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
      });
      res.end(body);
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("mock server did not bind a TCP port");
  }
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    requests,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

function sortJsonValue(value) {
  if (!value || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map(sortJsonValue);
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, sortJsonValue(value[key])]),
  );
}

function trustedHash({ command, timeout }) {
  const identity = {
    event_name: "pre_tool_use",
    hooks: [
      {
        async: false,
        command,
        statusMessage,
        timeout,
        type: "command",
      },
    ],
  };
  return `sha256:${createHash("sha256")
    .update(JSON.stringify(sortJsonValue(identity)))
    .digest("hex")}`;
}

function configToml(baseUrl) {
  return `model = "mock-model"
model_provider = "mock_provider"
approval_policy = "never"
sandbox_mode = "danger-full-access"

[features]
hooks = true
code_mode = true

[model_providers.mock_provider]
name = "Mock provider for code mode OpenClaw valid relay proof"
base_url = "${baseUrl}/v1"
wire_api = "responses"
request_max_retries = 0
stream_max_retries = 0
supports_websockets = false
`;
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function createMockPluginRegistry(hooks) {
  const pluginIds = [...new Set(hooks.map((hook) => hook.pluginId || "test-plugin"))];
  const typedHooks = hooks.map((hook) => ({
    pluginId: hook.pluginId || "test-plugin",
    hookName: hook.hookName,
    handler: hook.handler,
    priority: 0,
    source: "test",
  }));
  return {
    plugins: pluginIds.map((pluginId) => ({
      id: pluginId,
      name: "Test Plugin",
      source: "test",
      hookCount: typedHooks.filter((hook) => hook.pluginId === pluginId).length,
    })),
    hooks,
    typedHooks,
    tools: [],
    channels: [],
    channelSetups: [],
    providers: [],
    embeddingProviders: [],
    speechProviders: [],
    mediaUnderstandingProviders: [],
    transcriptSourceProviders: [],
    imageGenerationProviders: [],
    videoGenerationProviders: [],
    musicGenerationProviders: [],
    webFetchProviders: [],
    webSearchProviders: [],
    migrationProviders: [],
    codexAppServerExtensionFactories: [],
    agentToolResultMiddlewares: [],
    memoryEmbeddingProviders: [],
    agentHarnesses: [],
    httpRoutes: [],
    gatewayHandlers: {},
    cliRegistrars: [],
    textTransforms: [],
    reloads: [],
    nodeHostCommands: [],
    securityAuditCollectors: [],
    services: [],
    gatewayDiscoveryServices: [],
    conversationBindingResolvedHandlers: [],
    commands: [],
    diagnostics: [],
  };
}

async function main() {
  if (!existsSync(codexBin)) throw new Error(`Codex binary not found: ${codexBin}`);
  if (!existsSync(openclawMjs)) throw new Error(`OpenClaw entrypoint not found: ${openclawMjs}`);

  const [{ initializeGlobalHookRunner, resetGlobalHookRunner }, { registerNativeHookRelay }] =
    await Promise.all([
      import(pathToFileURL(join(openclawRoot, "dist/plugin-sdk/hook-runtime.js")).href),
      import(pathToFileURL(join(openclawRoot, "dist/plugin-sdk/agent-harness-runtime.js")).href),
    ]);

  const root = mkdtempSync(join(tmpdir(), "codex-code-mode-openclaw-valid-relay-"));
  const codexHome = join(root, "codex_home");
  const workspace = join(root, "workspace");
  const execMarker = join(root, "code-mode-exec-ran.txt");
  const beforeToolCalls = [];
  await Promise.all([mkdir(codexHome), mkdir(workspace)]);

  const blockReason = "valid relay proof blocks this command";
  initializeGlobalHookRunner(
    createMockPluginRegistry([
      {
        hookName: "before_tool_call",
        handler: async (event, context) => {
          beforeToolCalls.push({ event, context });
          return { block: true, blockReason };
        },
      },
    ]),
  );

  const relay = registerNativeHookRelay({
    provider: "codex",
    relayId: `valid-relay-proof-${process.pid}`,
    generation: `generation-${process.pid}`,
    agentId: "proof-agent",
    sessionId: "proof-session",
    sessionKey: "agent:proof-agent:proof-session",
    runId: "proof-run",
    channelId: "proof-channel",
    allowedEvents: ["pre_tool_use"],
    preToolUsePolicyActive: true,
    ttlMs: 60_000,
    command: {
      executable: openclawMjs,
      nodeExecutable: openclawNode,
      nice: false,
      timeoutMs: 5_000,
    },
  });

  const source =
    `const r = await tools.exec_command({cmd: ${JSON.stringify(
      `printf CODE_MODE_EXEC_RAN > ${execMarker}`,
    )}});\n` + "text('outer:' + r.output);";
  const mock = await startMockResponsesServer(source);
  writeFileSync(join(codexHome, "config.toml"), configToml(mock.baseUrl));

  const hookCommand = relay.commandForEvent("pre_tool_use");
  const timeout = 8;
  const hash = trustedHash({ command: hookCommand, timeout });
  const threadConfig = {
    "features.hooks": true,
    "features.code_mode": true,
    "features.unified_exec": true,
    experimental_use_unified_exec_tool: true,
    "hooks.PreToolUse": [
      {
        hooks: [
          {
            type: "command",
            command: hookCommand,
            timeout,
            async: false,
            statusMessage,
          },
        ],
      },
    ],
    "hooks.state": {
      "/<session-flags>/config.toml:pre_tool_use:0:0": {
        enabled: true,
        trusted_hash: hash,
      },
      "<session-flags>/config.toml:pre_tool_use:0:0": {
        enabled: true,
        trusted_hash: hash,
      },
    },
  };

  const stderr = [];
  const notifications = [];
  const child = spawn(codexBin, ["app-server", "--listen", "stdio://"], {
    cwd: codexHome,
    env: {
      ...process.env,
      CODEX_HOME: codexHome,
      CODEX_SQLITE_HOME: codexHome,
      OPENAI_API_KEY: "dummy",
      CODEX_API_KEY: "dummy",
      RUST_LOG:
        "warn,codex_hooks=trace,codex_core::hook_runtime=trace,codex_core::tools::router=trace",
    },
    stdio: ["pipe", "pipe", "pipe"],
  });
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => stderr.push(chunk));
  const rl = readline.createInterface({ input: child.stdout });
  let nextId = 1;
  const pending = new Map();
  rl.on("line", (line) => {
    if (!line.trim()) return;
    let message;
    try {
      message = JSON.parse(line);
    } catch (error) {
      notifications.push({ parseError: String(error), line });
      return;
    }
    if (message.id !== undefined && pending.has(message.id)) {
      const waiter = pending.get(message.id);
      pending.delete(message.id);
      clearTimeout(waiter.timer);
      waiter.resolve(message);
      return;
    }
    notifications.push(message);
  });

  function send(method, params, timeoutMs = 20_000) {
    const id = nextId++;
    child.stdin.write(`${JSON.stringify({ id, method, params })}\n`);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`timed out waiting for ${method}; stderr=${stderr.join("")}`));
      }, timeoutMs);
      pending.set(id, { resolve, reject, timer });
    });
  }

  function notify(method, params) {
    child.stdin.write(`${JSON.stringify(params === undefined ? { method } : { method, params })}\n`);
  }

  try {
    const init = await send("initialize", {
      clientInfo: { name: "code-mode-openclaw-valid-relay-proof", title: null, version: "0.1.0" },
      capabilities: { experimentalApi: true },
    });
    if (init.error) throw new Error(`initialize failed: ${JSON.stringify(init.error)}`);
    notify("notifications/initialized");

    const start = await send("thread/start", {
      model: "mock-model",
      cwd: workspace,
      config: threadConfig,
      experimentalRawEvents: true,
    });
    if (start.error) throw new Error(`thread/start failed: ${JSON.stringify(start.error)}`);

    const threadId = start.result?.thread?.id;
    const turn = await send("turn/start", {
      threadId,
      input: [{ type: "text", text: "run the code mode OpenClaw valid relay proof" }],
    });
    if (turn.error) throw new Error(`turn/start failed: ${JSON.stringify(turn.error)}`);

    for (let i = 0; i < 160; i += 1) {
      if (notifications.some((message) => message.method === "turn/completed")) break;
      await wait(250);
    }

    const hookEvents = notifications
      .filter((message) => typeof message.method === "string" && message.method.startsWith("hook/"))
      .map((message) => ({ method: message.method, params: message.params || null }));
    const result = {
      ok: false,
      threadId,
      turnId: turn.result?.turn?.id,
      relayId: relay.relayId,
      relayGeneration: relay.generation,
      trustedHash: hash,
      hookCommand,
      codexBin,
      openclawRoot,
      openclawMjs,
      execMarker,
      execMarkerExists: existsSync(execMarker),
      execMarkerContents: existsSync(execMarker) ? readFileSync(execMarker, "utf8") : null,
      requestCount: mock.requests.length,
      requestUrls: mock.requests.map((request) => request.url),
      notificationMethods: notifications.map((message) => message.method).filter(Boolean),
      hookEvents,
      beforeToolCallCount: beforeToolCalls.length,
      beforeToolCalls,
      stderrContainsCodeModeExec: stderr.join("").includes("Tool: code_mode_exec"),
      stderrContainsBlocked: stderr.join("").includes("Tool call blocked by PreToolUse hook"),
      stderrTail: stderr.join("").split("\n").filter(Boolean).slice(-30),
    };
    result.ok =
      !result.execMarkerExists &&
      result.beforeToolCallCount === 1 &&
      result.beforeToolCalls[0]?.event?.toolName === "code_mode_exec" &&
      result.stderrContainsCodeModeExec &&
      result.stderrContainsBlocked &&
      result.hookEvents.some((event) => event.method === "hook/completed");
    console.log(JSON.stringify(result, null, 2));
    if (!result.ok) process.exitCode = 1;
  } finally {
    resetGlobalHookRunner();
    relay.unregister();
    child.kill("SIGTERM");
    await new Promise((resolve) => child.once("exit", resolve));
    await mock.close();
    rmSync(root, { recursive: true, force: true });
  }
}

await main();
