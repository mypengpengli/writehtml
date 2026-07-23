#!/usr/bin/env node

// Stdio adapter around the official Pi Coding Agent SDK.  The Python service
// supplies writing-specific database tools; Pi keeps its normal tools,
// resource discovery, extension loading, package loading, and session loop.
import { existsSync, statSync } from "node:fs";
import { resolve } from "node:path";
import readline from "node:readline";
import OpenAI from "openai";
import { AssistantMessageEventStream, Type } from "@earendil-works/pi-ai";
import {
  createAgentSession,
  DefaultResourceLoader,
  defineTool,
  getAgentDir,
  ModelRuntime,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
let started = false;
let startRequest;
let resolveStart;
const startPromise = new Promise((resolveStartRequest) => {
  resolveStart = resolveStartRequest;
});
const toolWaiters = new Map();

function emit(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function messageText(error) {
  if (error instanceof Error) return error.message;
  return String(error || "Unknown Pi bridge error");
}

function awaitToolResult(toolCallId) {
  return new Promise((resolveResult, rejectResult) => {
    toolWaiters.set(toolCallId, { resolve: resolveResult, reject: rejectResult });
  });
}

input.on("line", (line) => {
  let command;
  try {
    command = JSON.parse(line);
  } catch {
    emit({ type: "protocol_error", error: "Invalid JSON command" });
    return;
  }

  if (command.type === "start") {
    if (started) {
      emit({ type: "protocol_error", error: "Only one start command is allowed" });
      return;
    }
    started = true;
    startRequest = command.request;
    resolveStart(startRequest);
    return;
  }

  if (command.type === "tool_result" && typeof command.toolCallId === "string") {
    const waiter = toolWaiters.get(command.toolCallId);
    if (!waiter) return;
    toolWaiters.delete(command.toolCallId);
    if (command.error) waiter.reject(new Error(String(command.error)));
    else waiter.resolve(command.result ?? {});
  }
});

input.on("close", () => {
  for (const waiter of toolWaiters.values()) {
    waiter.reject(new Error("Python host closed the Pi bridge input"));
  }
  toolWaiters.clear();
});

function textContent(value) {
  return [{ type: "text", text: JSON.stringify(value ?? {}) }];
}

function publicToolResult(result) {
  if (!result || typeof result !== "object" || Array.isArray(result)) return result ?? {};
  const copy = { ...result };
  delete copy._pi_system;
  delete copy._writehtmlBridge;
  return copy;
}

function workspacePath(request) {
  const candidate = typeof request.cwd === "string" && request.cwd.trim()
    ? resolve(request.cwd)
    : process.cwd();
  try {
    return existsSync(candidate) && statSync(candidate).isDirectory() ? candidate : process.cwd();
  } catch {
    return process.cwd();
  }
}

function contentText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part && part.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("");
}

function toOpenAIMessageParams(context) {
  const messages = [];
  if (context.systemPrompt) messages.push({ role: "system", content: context.systemPrompt });
  for (const message of context.messages || []) {
    if (!message || typeof message !== "object") continue;
    if (message.role === "user") {
      const audio = message._writehtmlAudio;
      if (audio && typeof audio.data === "string" && typeof audio.format === "string") {
        messages.push({
          role: "user",
          content: [
            { type: "text", text: audio.instruction || contentText(message.content) || "Voice instruction" },
            { type: "input_audio", input_audio: { data: audio.data, format: audio.format } },
          ],
        });
      } else {
        messages.push({ role: "user", content: contentText(message.content) });
      }
      continue;
    }
    if (message.role === "assistant") {
      const calls = Array.isArray(message.content)
        ? message.content.filter((part) => part && part.type === "toolCall")
        : [];
      messages.push({
        role: "assistant",
        content: contentText(message.content) || null,
        ...(calls.length
          ? { tool_calls: calls.map((call) => ({
              id: call.id,
              type: "function",
              function: { name: call.name, arguments: JSON.stringify(call.arguments || {}) },
            })) }
          : {}),
      });
      continue;
    }
    if (message.role === "toolResult") {
      messages.push({
        role: "tool",
        tool_call_id: message.toolCallId,
        content: contentText(message.content) || JSON.stringify(message.details || {}),
      });
    }
  }
  return messages;
}

function stopReason(finishReason) {
  if (finishReason === "tool_calls") return "toolUse";
  if (finishReason === "length") return "length";
  if (finishReason === "stop") return "stop";
  return "error";
}

function parseArguments(value) {
  try {
    const parsed = JSON.parse(value || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

// Pi's public model type currently represents text and images, while the app
// also supports OpenAI-compatible input_audio.  This keeps the Pi session and
// all native tools intact for direct-audio turns.
function directAudioStream(apiKey, fallbackBaseUrl) {
  return (model, context, options) => {
    const stream = new AssistantMessageEventStream();
    void (async () => {
      const output = {
        role: "assistant",
        content: [],
        api: model.api,
        provider: model.provider,
        model: model.id,
        usage: {
          input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
        },
        stopReason: "stop",
        timestamp: Date.now(),
      };
      try {
        const client = new OpenAI({
          apiKey,
          baseURL: model.baseUrl || fallbackBaseUrl,
          dangerouslyAllowBrowser: true,
        });
        const tools = (context.tools || []).map((tool) => ({
          type: "function",
          function: { name: tool.name, description: tool.description, parameters: tool.parameters },
        }));
        const completion = await client.chat.completions.create({
          model: model.id,
          messages: toOpenAIMessageParams(context),
          stream: true,
          ...(tools.length ? { tools, tool_choice: "auto" } : {}),
        }, { signal: options?.signal, maxRetries: 0 });
        stream.push({ type: "start", partial: output });
        let textBlock;
        const callsByIndex = new Map();
        let sawFinishReason = false;
        for await (const chunk of completion) {
          output.responseId ||= chunk.id;
          const usage = chunk.usage;
          if (usage) {
            output.usage.input = usage.prompt_tokens || 0;
            output.usage.output = usage.completion_tokens || 0;
            output.usage.totalTokens = usage.total_tokens || output.usage.input + output.usage.output;
          }
          const choice = chunk.choices?.[0];
          if (!choice) continue;
          if (choice.finish_reason) {
            output.stopReason = stopReason(choice.finish_reason);
            sawFinishReason = true;
          }
          const delta = choice.delta || {};
          if (typeof delta.content === "string" && delta.content) {
            if (!textBlock) {
              textBlock = { type: "text", text: "" };
              output.content.push(textBlock);
              stream.push({ type: "text_start", contentIndex: output.content.length - 1, partial: output });
            }
            textBlock.text += delta.content;
            stream.push({ type: "text_delta", contentIndex: output.content.indexOf(textBlock), delta: delta.content, partial: output });
          }
          for (const toolCall of delta.tool_calls || []) {
            const index = typeof toolCall.index === "number" ? toolCall.index : 0;
            let block = callsByIndex.get(index);
            if (!block) {
              block = { type: "toolCall", id: toolCall.id || "", name: toolCall.function?.name || "", arguments: {}, _partialArgs: "" };
              callsByIndex.set(index, block);
              output.content.push(block);
              stream.push({ type: "toolcall_start", contentIndex: output.content.length - 1, partial: output });
            }
            if (toolCall.id) block.id = toolCall.id;
            if (toolCall.function?.name) block.name = toolCall.function.name;
            const argumentDelta = toolCall.function?.arguments || "";
            block._partialArgs += argumentDelta;
            block.arguments = parseArguments(block._partialArgs);
            stream.push({ type: "toolcall_delta", contentIndex: output.content.indexOf(block), delta: argumentDelta, partial: output });
          }
        }
        if (!sawFinishReason) throw new Error("Audio model stream ended without finish_reason");
        if (textBlock) stream.push({ type: "text_end", contentIndex: output.content.indexOf(textBlock), content: textBlock.text, partial: output });
        for (const block of callsByIndex.values()) {
          block.arguments = parseArguments(block._partialArgs);
          delete block._partialArgs;
          stream.push({ type: "toolcall_end", contentIndex: output.content.indexOf(block), toolCall: block, partial: output });
        }
        stream.push({ type: "done", reason: output.stopReason, message: output });
        stream.end();
      } catch (error) {
        output.stopReason = options?.signal?.aborted ? "aborted" : "error";
        output.errorMessage = messageText(error);
        stream.push({ type: "error", reason: output.stopReason, error: output });
        stream.end();
      }
    })();
    return stream;
  };
}

function createWritingTool(spec) {
  const parameters = spec && typeof spec.parameters === "object" && !Array.isArray(spec.parameters)
    ? spec.parameters
    : { type: "object", properties: {} };
  return defineTool({
    name: spec.name,
    label: spec.label || spec.name,
    description: spec.description || spec.name,
    promptSnippet: `${spec.name}: ${spec.description || spec.name}`,
    parameters: Type.Unsafe(parameters),
    executionMode: "sequential",
    async execute(toolCallId, params) {
      emit({ type: "tool_call", toolCallId, name: spec.name, args: params || {} });
      const result = await awaitToolResult(toolCallId);
      const details = result && typeof result === "object" && !Array.isArray(result)
        ? { ...result, _writehtmlBridge: true }
        : { value: result, _writehtmlBridge: true };
      return { content: textContent(publicToolResult(result)), details };
    },
  });
}

function installWritingToolLifecycle(session) {
  const pendingSystemPrompts = [];
  const previousAfterToolCall = session.agent.afterToolCall;
  const previousPrepareNextTurn = session.agent.prepareNextTurnWithContext;

  session.agent.afterToolCall = async (context, signal) => {
    const details = context.result?.details;
    if (!details || typeof details !== "object" || !details._writehtmlBridge) {
      return previousAfterToolCall ? previousAfterToolCall(context, signal) : undefined;
    }
    if (typeof details._pi_system === "string" && details._pi_system) {
      pendingSystemPrompts.push(details._pi_system);
    }
    const publicDetails = publicToolResult(details);
    return {
      content: textContent(publicDetails),
      details: publicDetails,
      isError: Boolean(publicDetails && typeof publicDetails === "object" && publicDetails.error),
    };
  };

  session.agent.prepareNextTurnWithContext = async (turn, signal) => {
    const prepared = previousPrepareNextTurn ? await previousPrepareNextTurn(turn, signal) : undefined;
    if (!pendingSystemPrompts.length) return prepared;
    const additions = pendingSystemPrompts.splice(0).join("\n\n");
    const context = prepared?.context || turn.context;
    return {
      ...(prepared || {}),
      context: {
        ...context,
        systemPrompt: context.systemPrompt
          ? `${context.systemPrompt}\n\n${additions}`
          : additions,
      },
    };
  };
}

async function createPiSession(request) {
  const cwd = workspacePath(request);
  const agentDir = typeof request.agentDir === "string" && request.agentDir.trim()
    ? resolve(request.agentDir)
    : getAgentDir();
  const skillDirs = Array.isArray(request.skillDirs)
    ? request.skillDirs.filter((path) => typeof path === "string" && path.trim())
    : [];
  const settingsManager = SettingsManager.create(cwd, agentDir, { projectTrusted: true });
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir,
    settingsManager,
    additionalSkillPaths: skillDirs,
    appendSystemPrompt: request.systemPrompt ? [String(request.systemPrompt)] : [],
  });
  await resourceLoader.reload();

  const providerId = "writehtml-openai-compatible";
  const modelRuntime = await ModelRuntime.create();
  modelRuntime.registerProvider(providerId, {
    name: "WriteHTML OpenAI compatible",
    baseUrl: request.baseUrl,
    api: "openai-completions",
    apiKey: request.apiKey,
    authHeader: true,
    models: [{
      id: request.model,
      name: request.model,
      api: "openai-completions",
      baseUrl: request.baseUrl,
      reasoning: false,
      input: ["text", "image"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: request.contextWindow || 128000,
      maxTokens: request.maxTokens || 8192,
    }],
  });
  await modelRuntime.setRuntimeApiKey(providerId, request.apiKey);
  const model = modelRuntime.getModel(providerId, request.model);
  if (!model) throw new Error(`Pi could not resolve model ${request.model}`);

  const tools = Array.isArray(request.tools)
    ? request.tools.filter((spec) => spec && typeof spec.name === "string" && spec.name)
    : [];
  const { session } = await createAgentSession({
    cwd,
    agentDir,
    modelRuntime,
    model,
    settingsManager,
    sessionManager: SessionManager.inMemory(cwd),
    resourceLoader,
    customTools: tools.map(createWritingTool),
  });

  // The Pi SDK defaults to four coding tools.  This web agent deliberately
  // enables every discovered Pi tool, including grep/find/ls and tools exposed
  // by installed Pi extensions/packages.
  session.setActiveToolsByName(session.getAllTools().map((tool) => tool.name));
  session.agent.state.messages = Array.isArray(request.messages) ? request.messages : [];
  session.agent.sessionId = String(request.sessionId || session.agent.sessionId || "writehtml-pi");
  installWritingToolLifecycle(session);
  return session;
}

async function run() {
  const request = await startPromise;
  if (!request || typeof request !== "object") throw new Error("Missing Pi bridge request");
  if (!request.apiKey || !request.baseUrl || !request.model) {
    throw new Error("Pi bridge requires baseUrl, apiKey, and model");
  }

  let session;
  try {
    session = await createPiSession(request);
    if (request.audio) {
      session.agent.streamFunction = directAudioStream(request.apiKey, request.baseUrl);
      await session.agent.prompt({
        role: "user",
        content: [{ type: "text", text: String(request.prompt || "[voice] Voice instruction") }],
        timestamp: Date.now(),
        _writehtmlAudio: request.audio,
      });
    } else {
      await session.prompt(String(request.prompt || ""));
    }

    const messages = session.agent.state.messages.map((message) => {
      if (!message || typeof message !== "object" || !message._writehtmlAudio) return message;
      const clean = { ...message };
      delete clean._writehtmlAudio;
      return clean;
    });
    const finalAssistant = [...messages].reverse().find((message) => message.role === "assistant");
    if (finalAssistant?.stopReason === "error" || finalAssistant?.stopReason === "aborted") {
      throw new Error(finalAssistant.errorMessage || "Pi model request failed");
    }
    emit({ type: "done", messages });
  } finally {
    session?.dispose();
  }
}

run().catch((error) => {
  emit({ type: "fatal", error: messageText(error) });
  process.exitCode = 1;
});
