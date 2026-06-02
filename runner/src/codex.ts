import { Codex, type ThreadOptions } from "@openai/codex-sdk";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

interface AuthProcessState {
  status: "idle" | "pending" | "authenticated" | "failed";
  verificationUri?: string;
  userCode?: string;
  output: string;
  startedAt?: string;
  finishedAt?: string;
  error?: string;
}

export class CodexService {
  private authState: AuthProcessState = { status: "idle", output: "" };
  private authProcessRunning = false;

  constructor(
    private readonly codexHome: string,
    private readonly workspace: string,
    private readonly skillsPath: string
  ) {}

  async authStatus(): Promise<Record<string, unknown>> {
    const result = await runCodex(["login", "status"], this.codexHome);
    return {
      ok: result.code === 0,
      status: result.code === 0 ? "authenticated" : "unauthenticated",
      stdout: stripAnsi(result.stdout).trim(),
      stderr: stripAnsi(result.stderr).trim(),
      authProcess: this.authState
    };
  }

  async startDeviceAuth(): Promise<Record<string, unknown>> {
    const current = await runCodex(["login", "status"], this.codexHome);
    if (current.code === 0) {
      return {
        status: "already_authenticated",
        stdout: stripAnsi(current.stdout).trim()
      };
    }

    if (this.authProcessRunning) {
      return {
        ...this.authState,
        status: "pending"
      };
    }

    await mkdir(this.codexHome, { recursive: true, mode: 0o700 });
    this.authProcessRunning = true;
    this.authState = {
      status: "pending",
      output: "",
      startedAt: new Date().toISOString()
    };

    const child = spawn(codexBin(), ["login", "--device-auth"], {
      env: codexEnv(this.codexHome),
      cwd: this.workspace,
      stdio: ["ignore", "pipe", "pipe"]
    });

    child.stdout.on("data", (chunk) => this.captureAuthOutput(String(chunk)));
    child.stderr.on("data", (chunk) => this.captureAuthOutput(String(chunk)));
    child.on("error", (error) => {
      this.authProcessRunning = false;
      this.authState = {
        ...this.authState,
        status: "failed",
        error: error.message,
        finishedAt: new Date().toISOString()
      };
    });
    child.on("close", async (code) => {
      this.authProcessRunning = false;
      this.authState = {
        ...this.authState,
        status: code === 0 ? "authenticated" : "failed",
        finishedAt: new Date().toISOString(),
        error: code === 0 ? undefined : `codex login exited with ${code}`
      };
    });

    await waitForAuthCode(() => this.authState);
    return {
      ...this.authState,
      status: "started"
    };
  }

  async chat(prompt: string, model?: string): Promise<Record<string, unknown>> {
    await mkdir(this.workspace, { recursive: true });

    const codex = new Codex({
      env: codexEnv(this.codexHome)
    });
    const threadOptions: ThreadOptions = {
      sandboxMode: "read-only",
      workingDirectory: this.workspace,
      skipGitRepoCheck: true,
      approvalPolicy: "never",
      networkAccessEnabled: false
    };
    if (model) threadOptions.model = model;

    const thread = codex.startThread(threadOptions);
    const result = await thread.run(buildPrompt(prompt, this.skillsPath));

    return {
      text: result.finalResponse,
      usage: result.usage,
      threadId: thread.id
    };
  }

  private captureAuthOutput(chunk: string): void {
    const output = `${this.authState.output}${chunk}`;
    const cleanOutput = stripAnsi(output);
    const verificationUri = this.authState.verificationUri ?? parseVerificationUri(cleanOutput);
    const userCode = this.authState.userCode ?? parseUserCode(cleanOutput);
    this.authState = {
      ...this.authState,
      output: cleanOutput.slice(-4000),
      verificationUri,
      userCode
    };
  }
}

async function runCodex(args: string[], codexHome: string): Promise<{
  code: number | null;
  stdout: string;
  stderr: string;
}> {
  return new Promise((resolve) => {
    const child = spawn(codexBin(), args, {
      env: codexEnv(codexHome),
      stdio: ["ignore", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => stdout += String(chunk));
    child.stderr.on("data", (chunk) => stderr += String(chunk));
    child.on("error", (error) => resolve({ code: 1, stdout, stderr: error.message }));
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

function buildPrompt(prompt: string, skillsPath: string): string {
  return [
    "You are KabuBot, a stock monitoring analysis agent.",
    "Use the skill files in this directory as operating guidance when relevant:",
    skillsPath,
    "The market data and X-search evidence are already supplied in the user prompt.",
    "Do not invent prices, tweets, filings, or catalysts. Mark uncertainty clearly.",
    "This is research support, not financial advice. Avoid imperative buy/sell instructions.",
    "",
    prompt
  ].join("\n");
}

function codexEnv(codexHome: string): Record<string, string> {
  return compactEnv({
    ...process.env,
    HOME: process.env.HOME || "/home/codex",
    CODEX_HOME: codexHome
  });
}

function codexBin(): string {
  const local = join(process.cwd(), "node_modules", ".bin", process.platform === "win32" ? "codex.cmd" : "codex");
  return existsSync(local) ? local : "codex";
}

function compactEnv(env: NodeJS.ProcessEnv): Record<string, string> {
  return Object.fromEntries(
    Object.entries(env).filter((entry): entry is [string, string] => entry[1] !== undefined)
  );
}

function parseVerificationUri(output: string): string | undefined {
  return output.match(/https:\/\/[^\s]+/)?.[0].replace(/[).,;]+$/, "");
}

function parseUserCode(output: string): string | undefined {
  return output.match(/\b[A-Z0-9]{4,}-[A-Z0-9-]{4,}\b/)?.[0];
}

function stripAnsi(value: string): string {
  return value.replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, "");
}

async function waitForAuthCode(getState: () => AuthProcessState): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const state = getState();
    if (state.userCode || state.status !== "pending") return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}
