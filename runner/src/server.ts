import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { mkdir } from "node:fs/promises";
import { CodexService } from "./codex.js";

const port = Number(process.env.PORT ?? 8789);
const codexHome = process.env.CODEX_HOME || "/home/codex/.codex";
const workspace = process.env.WORKSPACE_DIR || "/workspace";
const skillsPath = process.env.KABUBOT_SKILLS_PATH || "/workspace/skills";

const codex = new CodexService(codexHome, workspace, skillsPath);

await mkdir(codexHome, { recursive: true, mode: 0o700 });
await mkdir(workspace, { recursive: true });

createServer(async (request, response) => {
  try {
    if (request.url === "/health") {
      return sendJson(response, 200, { ok: true });
    }

    if (request.method === "GET" && request.url === "/auth/status") {
      return sendJson(response, 200, await codex.authStatus());
    }

    if (request.method === "POST" && request.url === "/auth/start") {
      return sendJson(response, 200, await codex.startDeviceAuth());
    }

    if (request.method === "POST" && request.url === "/chat") {
      const body = await readJson<{ prompt?: string; model?: string }>(request);
      if (!body.prompt) return sendJson(response, 400, { error: "prompt is required" });
      return sendJson(response, 200, await codex.chat(body.prompt, body.model || process.env.CODEX_MODEL));
    }

    return sendJson(response, 404, { error: "not found" });
  } catch (error) {
    console.error(error);
    return sendJson(response, 500, {
      error: error instanceof Error ? error.message : String(error)
    });
  }
}).listen(port, "0.0.0.0", () => {
  console.log(`kabubot codex runner listening on ${port}`);
});

async function readJson<T>(request: IncomingMessage): Promise<T> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  const text = Buffer.concat(chunks).toString("utf8");
  return text ? JSON.parse(text) as T : {} as T;
}

function sendJson(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8"
  });
  response.end(JSON.stringify(body));
}
