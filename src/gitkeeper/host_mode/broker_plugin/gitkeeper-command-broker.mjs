// GitKeeper command broker plugin for OpenCode.
//
// OpenCode calls `tool.execute.before` and awaits it before running the tool, so
// a hook that never answers keeps the tool from running, and a hook that throws
// fails the tool call. This plugin uses both properties: every shell request is
// sent to the GitKeeper companion, and anything other than an explicit approval
// for *this exact request* throws. Deny, timeout, unreachable companion,
// malformed reply, missing configuration and unknown shell tool ids all block.
//
// The plugin never decides on its own and never rewrites a command. It also
// never logs the command text, which keeps PR content out of logs.

import { readFile } from "node:fs/promises";

const SHELL_TOOL_PATTERN =
  /(^|[._-])(bash|sh|zsh|fish|shell|exec|execute|command|cmd|powershell|pwsh|terminal|run|spawn)([._-]|$)/i;

const COMMAND_KEYS = ["command", "cmd", "script", "commandLine"];

const DEFAULT_TIMEOUT_MS = 120_000;

const REQUEST_PATH = "/command-request";

class BrokerBlockedError extends Error {
  constructor(reason) {
    super(`GitKeeper blocked this tool call: ${reason}`);
    this.name = "BrokerBlockedError";
    this.reason = reason;
  }
}

function looksLikeShellCall(tool, args) {
  if (typeof tool === "string" && SHELL_TOOL_PATTERN.test(tool)) {
    return true;
  }
  if (args && typeof args === "object") {
    return COMMAND_KEYS.some((key) => typeof args[key] === "string");
  }
  return false;
}

function commandText(args) {
  if (!args || typeof args !== "object") {
    return "";
  }
  for (const key of COMMAND_KEYS) {
    if (typeof args[key] === "string") {
      return args[key];
    }
  }
  return "";
}

// OpenCode hands the tool arguments in the hook's *output* parameter
// (`{ args }`), not in the input. Reading only `input.args` still blocks, but it
// blocks with an empty command, so the owner would approve text the model never
// asked for. Accept both shapes and treat a missing command as unrecognised.
function toolArguments(input, output) {
  const fromOutput = output && typeof output === "object" ? output.args : undefined;
  if (fromOutput && typeof fromOutput === "object") {
    return fromOutput;
  }
  const fromInput = input && typeof input === "object" ? input.args : undefined;
  return fromInput && typeof fromInput === "object" ? fromInput : undefined;
}

function randomRequestId() {
  return globalThis.crypto.randomUUID();
}

export function createBrokerHooks(options = {}) {
  const fetchImpl = options.fetch ?? globalThis.fetch;
  const env = options.env ?? process.env;
  const readFileImpl = options.readFile ?? readFile;
  const newRequestId = options.newRequestId ?? randomRequestId;
  const directory = options.directory ?? process.cwd();

  async function readToken() {
    const tokenFile = env.GITKEEPER_BROKER_TOKEN_FILE;
    if (!tokenFile) {
      throw new BrokerBlockedError("the companion token file is not configured");
    }
    let token;
    try {
      token = (await readFileImpl(tokenFile, "utf8")).trim();
    } catch {
      throw new BrokerBlockedError("the companion token file is unreadable");
    }
    if (!token) {
      throw new BrokerBlockedError("the companion token file is empty");
    }
    return token;
  }

  async function askCompanion(describe) {
    const baseUrl = env.GITKEEPER_BROKER_URL;
    if (!baseUrl) {
      throw new BrokerBlockedError("the companion broker is not configured");
    }
    const timeoutMs = Number(env.GITKEEPER_BROKER_TIMEOUT_MS ?? DEFAULT_TIMEOUT_MS);
    const token = await readToken();
    const requestId = newRequestId();
    let url;
    try {
      url = new URL(`${baseUrl.replace(/\/+$/, "")}${REQUEST_PATH}`);
    } catch {
      throw new BrokerBlockedError("the companion broker URL is not valid");
    }

    let response;
    try {
      response = await fetchImpl(url, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          request_id: requestId,
          tool: describe.tool,
          session_id: describe.sessionID,
          call_id: describe.callID,
          cwd: directory,
          command: describe.command,
        }),
        signal: AbortSignal.timeout(
          Number.isFinite(timeoutMs) ? timeoutMs : DEFAULT_TIMEOUT_MS,
        ),
      });
    } catch {
      throw new BrokerBlockedError("the companion did not answer");
    }

    if (!response || response.ok !== true) {
      const status = response ? `status ${response.status}` : "no response";
      throw new BrokerBlockedError(`the companion rejected the request (${status})`);
    }

    let decision;
    try {
      decision = await response.json();
    } catch {
      throw new BrokerBlockedError("the companion reply was not readable");
    }

    if (!decision || decision.request_id !== requestId) {
      throw new BrokerBlockedError("the companion replied about a different request");
    }
    if (decision.decision !== "approve") {
      throw new BrokerBlockedError(`the owner replied ${String(decision.decision)}`);
    }
  }

  return {
    "tool.execute.before": async (input, output) => {
      const tool = input?.tool;
      const args = toolArguments(input, output);
      if (!looksLikeShellCall(tool, args)) {
        return;
      }
      const command = commandText(args);
      if (!command) {
        throw new BrokerBlockedError(
          "the shell request carried no command the owner could approve",
        );
      }
      await askCompanion({
        tool: String(tool ?? "unknown"),
        sessionID: input?.sessionID ?? "",
        callID: input?.callID ?? "",
        command,
      });
    },
  };
}

export const GitKeeperCommandBroker = async (input) =>
  createBrokerHooks({ directory: input?.directory });

export default GitKeeperCommandBroker;
