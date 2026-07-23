import { Codex, type ThreadOptions } from "@openai/codex-sdk";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import readline from "node:readline";

interface AuthProcessState {
  status: "idle" | "pending" | "authenticated" | "failed";
  verificationUri?: string;
  userCode?: string;
  output: string;
  startedAt?: string;
  finishedAt?: string;
  error?: string;
}

interface AuthProbeResult {
  status: "authenticated" | "unauthenticated" | "expired" | "unavailable";
  method?: string;
  planType?: string;
  error?: string;
}

interface AppServerMessage {
  id?: number;
  result?: Record<string, unknown>;
  error?: {
    code?: number;
    message?: string;
  };
}

export class CodexService {
  private authState: AuthProcessState = { status: "idle", output: "" };
  private authProcessRunning = false;
  private readonly executable = codexBin();

  constructor(
    private readonly codexHome: string,
    private readonly workspace: string,
    private readonly skillsPath: string
  ) {}

  async authStatus(): Promise<Record<string, unknown>> {
    const local = await runCodex(["login", "status"], this.codexHome, this.executable);
    if (local.code !== 0) {
      this.markStoredAuthInvalid("Codex credentials are not present.");
      return {
        ok: false,
        status: "unauthenticated",
        stdout: stripAnsi(local.stdout).trim(),
        stderr: stripAnsi(local.stderr).trim(),
        validation: "local",
        authProcess: this.authState
      };
    }

    const probe = await probeCodexAuth(this.codexHome, this.executable);
    if (probe.status !== "authenticated") {
      this.markStoredAuthInvalid(probe.error || `Codex authentication is ${probe.status}.`);
    }
    return {
      ok: probe.status === "authenticated",
      status: probe.status,
      method: probe.method,
      planType: probe.planType,
      stdout: stripAnsi(local.stdout).trim(),
      stderr: probe.error || stripAnsi(local.stderr).trim(),
      validation: "remote",
      authProcess: this.authState
    };
  }

  async startDeviceAuth(): Promise<Record<string, unknown>> {
    const current = await this.authStatus();
    if (current.status === "authenticated") {
      return {
        status: "already_authenticated",
        stdout: current.stdout
      };
    }
    if (current.status === "unavailable") {
      return {
        status: "validation_unavailable",
        error: current.stderr || "Codex authentication could not be checked."
      };
    }

    if (this.authProcessRunning) {
      return {
        ...this.authState,
        status: "pending"
      };
    }

    if (current.status === "expired") {
      const logout = await runCodex(["logout"], this.codexHome, this.executable);
      if (logout.code !== 0) {
        return {
          status: "failed",
          error: stripAnsi(logout.stderr || logout.stdout).trim() || "Failed to clear expired Codex credentials."
        };
      }
    }

    await mkdir(this.codexHome, { recursive: true, mode: 0o700 });
    this.authProcessRunning = true;
    this.authState = {
      status: "pending",
      output: "",
      startedAt: new Date().toISOString()
    };

    const child = spawn(this.executable, ["login", "--device-auth"], {
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
      if (code !== 0) {
        this.authState = {
          ...this.authState,
          status: "failed",
          finishedAt: new Date().toISOString(),
          error: `codex login exited with ${code}`
        };
        return;
      }
      const verified = await this.authStatus();
      const authenticated = verified.status === "authenticated";
      this.authState = {
        ...this.authState,
        status: authenticated ? "authenticated" : "failed",
        finishedAt: new Date().toISOString(),
        error: authenticated
          ? undefined
          : String(verified.stderr || `Codex login completed but validation returned ${verified.status}.`)
      };
    });

    await waitForAuthCode(() => this.authState);
    const state = this.authState;
    return {
      ...state,
      status: state.status === "pending" ? "started" : state.status
    };
  }

  async chat(prompt: string, model?: string): Promise<Record<string, unknown>> {
    await mkdir(this.workspace, { recursive: true });

    const codex = new Codex({
      codexPathOverride: this.executable,
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

  private markStoredAuthInvalid(error: string): void {
    if (this.authState.status !== "authenticated") return;
    this.authState = {
      ...this.authState,
      status: "failed",
      error,
      finishedAt: new Date().toISOString()
    };
  }
}

async function runCodex(args: string[], codexHome: string): Promise<{
  code: number | null;
  stdout: string;
  stderr: string;
}>;
async function runCodex(args: string[], codexHome: string, executable: string): Promise<{
  code: number | null;
  stdout: string;
  stderr: string;
}>;
async function runCodex(
  args: string[],
  codexHome: string,
  executable = codexBin()
): Promise<{
  code: number | null;
  stdout: string;
  stderr: string;
}> {
  return new Promise((resolve) => {
    const child = spawn(executable, args, {
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

async function probeCodexAuth(codexHome: string, executable: string): Promise<AuthProbeResult> {
  return new Promise((resolve) => {
    const child = spawn(executable, ["app-server"], {
      env: codexEnv(codexHome),
      stdio: ["pipe", "pipe", "pipe"]
    });
    const lines = readline.createInterface({ input: child.stdout });
    let stderr = "";
    let accountMethod: string | undefined;
    let settled = false;

    const finish = (result: AuthProbeResult): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      lines.close();
      try {
        child.kill();
      } catch {
        // Process may already have exited.
      }
      resolve(result);
    };

    const timeout = setTimeout(() => {
      finish({
        status: "unavailable",
        method: accountMethod,
        error: "Timed out while validating Codex credentials."
      });
    }, 20_000);

    child.stderr.on("data", (chunk) => {
      stderr = `${stderr}${String(chunk)}`.slice(-4000);
    });
    child.on("error", (error) => {
      finish({ status: "unavailable", error: error.message });
    });
    child.on("close", (code) => {
      if (settled) return;
      finish({
        status: "unavailable",
        method: accountMethod,
        error: stripAnsi(stderr).trim() || `Codex app-server exited with ${code}.`
      });
    });

    lines.on("line", (line) => {
      let message: AppServerMessage;
      try {
        message = JSON.parse(line) as AppServerMessage;
      } catch {
        return;
      }

      if (message.id === 0) {
        if (message.error) {
          finish(classifyAuthProbeError(message.error.message, stderr));
          return;
        }
        sendAppServerMessage(child, { method: "initialized", params: {} });
        sendAppServerMessage(child, {
          method: "account/read",
          id: 1,
          params: { refreshToken: true }
        });
        return;
      }

      if (message.id === 1) {
        if (message.error) {
          finish(classifyAuthProbeError(message.error.message, stderr));
          return;
        }
        const account = asRecord(message.result?.account);
        if (!account) {
          finish({ status: "unauthenticated" });
          return;
        }
        accountMethod = typeof account.type === "string" ? account.type : undefined;
        sendAppServerMessage(child, {
          method: "account/rateLimits/read",
          id: 2
        });
        return;
      }

      if (message.id === 2) {
        if (message.error) {
          finish(classifyAuthProbeError(message.error.message, stderr, accountMethod));
          return;
        }
        const rateLimits = asRecord(message.result?.rateLimits);
        finish({
          status: "authenticated",
          method: accountMethod,
          planType: typeof rateLimits?.planType === "string" ? rateLimits.planType : undefined
        });
      }
    });

    sendAppServerMessage(child, {
      method: "initialize",
      id: 0,
      params: {
        clientInfo: {
          name: "kabubot_auth_probe",
          title: "KabuBot Auth Probe",
          version: "0.1.0"
        },
        capabilities: null
      }
    });
  });
}

function sendAppServerMessage(
  child: ChildProcessWithoutNullStreams,
  message: Record<string, unknown>
): void {
  child.stdin.write(`${JSON.stringify(message)}\n`);
}

export function classifyAuthProbeError(
  message: string | undefined,
  stderr: string,
  method?: string
): AuthProbeResult {
  const error = stripAnsi([message, stderr].filter(Boolean).join("\n")).trim() || "Codex authentication validation failed.";
  const authFailure = [
    /\b401\b/i,
    /unauthori[sz]ed/i,
    /authentication (?:is )?required/i,
    /not (?:logged|signed) in/i,
    /(?:invalid|expired|revoked).{0,40}(?:token|credential)/i,
    /(?:token|credential).{0,40}(?:invalid|expired|revoked)/i,
    /refresh token.{0,40}(?:already been used|missing|malformed)/i,
    /re-?authenticate/i,
    /login required/i,
    /sign in required/i
  ].some((pattern) => pattern.test(error));
  return {
    status: authFailure ? "expired" : "unavailable",
    method,
    error
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
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
