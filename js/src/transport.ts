/**
 * Transport layer for Docker Sandboxes (`sbx`).
 *
 * {@link SbxTransport} is the seam between {@link SbxSandbox} and whatever
 * actually talks to Docker Sandboxes. {@link CliSbxTransport} shells out to the
 * `sbx` CLI, the only supported interface for local sandboxes. A cloud
 * transport (own REST client or the official `@docker/sandboxes` SDK) can be
 * dropped in later without touching `SbxSandbox`.
 *
 * Host commands are always spawned with an argv array (never a host shell), and
 * output is streamed with a hard cap: the child is killed the moment the cap is
 * reached rather than buffered and trimmed.
 */

import { spawn } from "node:child_process";
import {
  SbxAuthError,
  SbxCommandError,
  SbxError,
  SbxNotFoundError,
  SbxNotInstalledError,
  SbxPolicyError,
  SbxShapeError,
  SbxTimeoutError,
} from "./errors.js";

export const DEFAULT_MAX_OUTPUT_BYTES = 512_000;
/** Exit status reported by coreutils `timeout(1)` when it kills a command. */
const TIMEOUT_EXIT_CODE = 124;

/** Billable Docker Cloud Sandboxes shapes, in MiB. */
export const CLOUD_SHAPES: Record<string, { cpus: number; memoryMib: number }> = {
  micro: { cpus: 1, memoryMib: 2048 },
  small: { cpus: 2, memoryMib: 4096 },
  medium: { cpus: 4, memoryMib: 8192 },
  large: { cpus: 8, memoryMib: 16384 },
  xl: { cpus: 16, memoryMib: 32768 },
};

const CLOUD_DEFAULT_CPUS = 2;
const CLOUD_DEFAULT_MEMORY = "4g";

const MEMORY_UNITS: Record<string, number> = {
  b: 1,
  k: 1024,
  kb: 1024,
  ki: 1024,
  kib: 1024,
  m: 1024 ** 2,
  mb: 1024 ** 2,
  mi: 1024 ** 2,
  mib: 1024 ** 2,
  g: 1024 ** 3,
  gb: 1024 ** 3,
  gi: 1024 ** 3,
  gib: 1024 ** 3,
  t: 1024 ** 4,
  tb: 1024 ** 4,
  ti: 1024 ** 4,
  tib: 1024 ** 4,
};

/** Parse a binary memory string (`"4g"`, `"8192MiB"`, `"2048"`) to MiB. */
export function parseMemoryMib(memory: string): number | null {
  const text = memory.trim().toLowerCase();
  if (!text) return null;
  let index = text.length;
  while (index > 0 && /[a-z]/.test(text[index - 1] ?? "")) index -= 1;
  const number = text.slice(0, index);
  const unit = text.slice(index);
  const value = Number(number);
  if (!Number.isFinite(value)) return null;
  if (!unit) return Math.trunc(value); // bare numbers are MiB
  const multiplier = MEMORY_UNITS[unit];
  if (multiplier === undefined) return null;
  return Math.trunc((value * multiplier) / 1024 ** 2);
}

/** Map a `(cpus, memory)` pair onto a billable cloud shape name. */
export function resolveCloudShape(cpus?: number, memory?: string): string {
  const effectiveCpus = cpus ?? CLOUD_DEFAULT_CPUS;
  const effectiveMemory = memory ?? CLOUD_DEFAULT_MEMORY;
  const memoryMib = parseMemoryMib(effectiveMemory);
  if (memoryMib === null) throw new SbxShapeError(`Could not parse cloud memory value '${memory}'.`);
  for (const [name, shape] of Object.entries(CLOUD_SHAPES)) {
    if (shape.cpus === effectiveCpus && shape.memoryMib === memoryMib) return name;
  }
  const options = Object.entries(CLOUD_SHAPES)
    .map(([name, shape]) => `${name} (${shape.cpus} vCPU/${shape.memoryMib} MiB)`)
    .join(", ");
  throw new SbxShapeError(
    `${effectiveCpus} vCPU / ${effectiveMemory} is not a billable cloud shape. Valid shapes: ${options}.`,
  );
}

/** Raw result of one `sbx` invocation. */
export interface CommandResult {
  readonly argv: readonly string[];
  readonly output: string;
  readonly exitCode: number | null;
  readonly truncated: boolean;
}

/** One row of `sbx ls --json`. */
export interface SandboxInfo {
  readonly id: string;
  readonly name: string;
  readonly status?: string;
  readonly agent?: string;
  readonly raw: Record<string, unknown>;
}

export interface ExecOptions {
  timeout?: number;
  maxOutputBytes?: number;
}

export interface CreateOptions {
  agent?: string;
  workspace?: string;
  cpus?: number;
  memory?: string;
  profile?: string;
  pull?: string;
  /** Cloud-only time-to-live, e.g. `"2h"`. */
  ttl?: string;
  /** Cloud-only behaviour when `ttl` lapses: `"delete"` or `"stop"`. */
  onTimeout?: string;
}

export interface RemoveOptions {
  force?: boolean;
}

/** Abstract operations {@link SbxSandbox} needs from Docker Sandboxes. */
export interface SbxTransport {
  /** Whether this transport targets Docker Cloud Sandboxes. */
  readonly cloud?: boolean;
  exec(sandbox: string, command: string, options?: ExecOptions): Promise<CommandResult>;
  upload(sandbox: string, localPath: string, remotePath: string): Promise<CommandResult>;
  download(sandbox: string, remotePath: string, localPath: string): Promise<CommandResult>;
  create(name: string, options?: CreateOptions): Promise<CommandResult>;
  remove(name: string, options?: RemoveOptions): Promise<CommandResult>;
  list(): Promise<SandboxInfo[]>;
  inspect(name: string): Promise<Record<string, unknown> | null>;
  exists(name: string): Promise<boolean>;
  /** Cloud-only: current TTL/expiration. */
  ttl(sandbox: string): Promise<Record<string, unknown> | null>;
  /** Cloud-only: extend the TTL by `duration` (e.g. `"2h"`). */
  extendTtl(sandbox: string, duration: string): Promise<Record<string, unknown> | null>;
}

const AUTH_MARKERS = [
  "not logged in",
  "not authenticated",
  "you are not logged in",
  "please log in",
  "please run sbx login",
  "run 'sbx login'",
  'run "sbx login"',
  "authentication required",
  "unauthorized",
  "no credentials",
];

const POLICY_MARKERS = ["global network policy has not been initialized", "sbx policy init"];

const NOT_FOUND_MARKERS = ["no such sandbox", "sandbox not found", "no sandbox named", "does not exist", "not found"];

const LOGIN_HINT = "Run 'sbx login' first.";
const POLICY_HINT = "Run 'sbx policy init <allow-all|balanced|deny-all>' first.";

/** Map a non-zero {@link CommandResult} onto the most specific error. */
export function classifyFailure(result: CommandResult): SbxError {
  const text = result.output.toLowerCase();
  if (POLICY_MARKERS.some((marker) => text.includes(marker))) {
    return new SbxPolicyError(`${result.output.trim()}\n${POLICY_HINT}`);
  }
  if (AUTH_MARKERS.some((marker) => text.includes(marker))) {
    return new SbxAuthError(`${result.output.trim()}\n${LOGIN_HINT}`);
  }
  if (NOT_FOUND_MARKERS.some((marker) => text.includes(marker))) {
    return new SbxNotFoundError(result.output.trim() || "Sandbox not found.");
  }
  return new SbxCommandError(result.output.trim() || `Command failed with exit code ${result.exitCode}`, {
    argv: result.argv,
    exitCode: result.exitCode,
    output: result.output,
  });
}

/** Parse the JSON emitted by `sbx ls --json`. */
export function parseSandboxList(payload: string): SandboxInfo[] {
  const text = payload.trim();
  if (!text) return [];
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (error) {
    throw new SbxCommandError(`Could not parse 'sbx ls --json' output: ${String(error)}`, { output: payload });
  }

  const records =
    data !== null && typeof data === "object" && !Array.isArray(data)
      ? ((data as Record<string, unknown>).sandboxes ?? [])
      : data;
  if (!Array.isArray(records)) return [];

  const infos: SandboxInfo[] = [];
  for (const record of records) {
    if (record === null || typeof record !== "object") continue;
    const raw = record as Record<string, unknown>;
    const name = String(raw.name ?? raw.id ?? "");
    if (!name) continue;
    infos.push({
      id: String(raw.id ?? name),
      name,
      status: raw.status !== undefined ? String(raw.status) : raw.state !== undefined ? String(raw.state) : undefined,
      agent: raw.agent !== undefined ? String(raw.agent) : undefined,
      raw,
    });
  }
  return infos;
}

interface RunOptions {
  timeout?: number;
  maxOutputBytes?: number;
}

export interface CliSbxTransportOptions {
  /** Extra environment variables merged over `process.env`. */
  env?: NodeJS.ProcessEnv;
  /**
   * When `true` (default), a sandbox-side `timeout` wraps long commands so the
   * remote process dies too. The host kill is always a backstop.
   */
  remoteTimeout?: boolean;
  /** Seconds the sandbox-side `timeout` waits after SIGTERM before SIGKILL. */
  remoteKillAfter?: number;
  /** Target Docker Cloud Sandboxes (`sbx --cloud …`) instead of local `sandboxd`. */
  cloud?: boolean;
}

/**
 * Transport that shells out to the `sbx` CLI.
 *
 * Works for local and Docker Cloud Sandboxes: `cloud: true` injects the global
 * `--cloud` flag so every verb is dispatched to the Cloud API.
 */
export class CliSbxTransport implements SbxTransport {
  readonly cloud: boolean;

  constructor(
    readonly binary: string = "sbx",
    private readonly options: CliSbxTransportOptions = {},
  ) {
    this.cloud = options.cloud ?? false;
  }

  private get remoteTimeout(): boolean {
    return this.options.remoteTimeout ?? true;
  }

  private get remoteKillAfter(): number {
    return Math.max(1, this.options.remoteKillAfter ?? 5);
  }

  private globalFlags(): string[] {
    return this.cloud ? ["--cloud"] : [];
  }

  private run(argv: string[], options: RunOptions): Promise<CommandResult> {
    const maxOutputBytes = options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES;
    const timeout = options.timeout !== undefined && options.timeout > 0 ? options.timeout : undefined;
    const full = [...this.globalFlags(), ...argv];

    return new Promise<CommandResult>((resolve, reject) => {
      const child = spawn(this.binary, full, {
        stdio: ["ignore", "pipe", "pipe"],
        env: this.options.env ? { ...process.env, ...this.options.env } : process.env,
      });

      const chunks: Buffer[] = [];
      let size = 0;
      let truncated = false;
      let timedOut = false;
      let settled = false;

      const finish = (fn: () => void): void => {
        if (settled) return;
        settled = true;
        fn();
      };

      const kill = (): void => {
        if (child.exitCode !== null || child.signalCode !== null) return;
        child.kill("SIGKILL");
      };

      const collect = (chunk: Buffer): void => {
        if (truncated) return;
        const remaining = maxOutputBytes - size;
        if (remaining <= 0) {
          truncated = true;
          kill();
          return;
        }
        if (chunk.length > remaining) {
          chunks.push(chunk.subarray(0, remaining));
          size = maxOutputBytes;
          truncated = true;
          kill();
          return;
        }
        chunks.push(chunk);
        size += chunk.length;
      };

      child.stdout?.on("data", collect);
      child.stderr?.on("data", collect);

      const timer =
        timeout !== undefined
          ? setTimeout(() => {
              timedOut = true;
              kill();
            }, timeout * 1000)
          : undefined;

      child.on("error", (error: NodeJS.ErrnoException) => {
        if (timer !== undefined) clearTimeout(timer);
        finish(() => {
          if (error.code === "ENOENT") {
            reject(
              new SbxNotInstalledError(
                `'${this.binary}' was not found on PATH. Install Docker Sandboxes and make sure the 'sbx' CLI is available.`,
              ),
            );
            return;
          }
          reject(new SbxError(`Failed to start '${this.binary}': ${error.message}`, { cause: error }));
        });
      });

      child.on("close", (code) => {
        if (timer !== undefined) clearTimeout(timer);
        const output = Buffer.concat(chunks).toString("utf8");
        finish(() => {
          if (timedOut) {
            reject(
              new SbxTimeoutError(
                `Command timed out after ${timeout}s: ${[this.binary, ...full].slice(0, 4).join(" ")} ...`,
                {
                  argv: [this.binary, ...full],
                  exitCode: null,
                  output,
                },
              ),
            );
            return;
          }
          resolve({ argv: [this.binary, ...full], output, exitCode: code, truncated });
        });
      });
    });
  }

  private async check(result: CommandResult): Promise<CommandResult> {
    if (result.exitCode !== 0) throw classifyFailure(result);
    return result;
  }

  private timeoutPrefix(timeout: number | undefined): string[] {
    if (!this.remoteTimeout || timeout === undefined || timeout <= 0) return [];
    return ["timeout", "-k", `${this.remoteKillAfter}s`, `${Math.trunc(timeout)}s`];
  }

  async exec(sandbox: string, command: string, options: ExecOptions = {}): Promise<CommandResult> {
    const timeout = options.timeout !== undefined && options.timeout > 0 ? options.timeout : undefined;
    const prefix = this.timeoutPrefix(timeout);
    const hostTimeout = prefix.length > 0 && timeout !== undefined ? timeout + this.remoteKillAfter + 1 : timeout;

    let result: CommandResult;
    try {
      result = await this.run(["exec", sandbox, ...prefix, "sh", "-c", command], {
        timeout: hostTimeout,
        maxOutputBytes: options.maxOutputBytes,
      });
    } catch (error) {
      if (error instanceof SbxTimeoutError) {
        throw new SbxTimeoutError(`Command timed out after ${timeout}s inside sandbox '${sandbox}'.`, {
          argv: error.argv,
          exitCode: null,
          output: error.output,
        });
      }
      throw error;
    }

    if (result.exitCode === TIMEOUT_EXIT_CODE && prefix.length > 0) {
      throw new SbxTimeoutError(`Command timed out after ${timeout}s inside sandbox '${sandbox}'.`, {
        argv: result.argv,
        exitCode: TIMEOUT_EXIT_CODE,
        output: result.output,
      });
    }
    return result;
  }

  async upload(sandbox: string, localPath: string, remotePath: string): Promise<CommandResult> {
    return this.check(await this.run(["cp", localPath, `${sandbox}:${remotePath}`], {}));
  }

  async download(sandbox: string, remotePath: string, localPath: string): Promise<CommandResult> {
    return this.check(await this.run(["cp", `${sandbox}:${remotePath}`, localPath], {}));
  }

  async create(name: string, options: CreateOptions = {}): Promise<CommandResult> {
    if (this.cloud) return this.createCloud(name, options);
    const args = ["create", "--name", name];
    if (options.cpus) args.push("--cpus", String(options.cpus));
    if (options.memory) args.push("--memory", options.memory);
    if (options.profile) args.push("--profile", options.profile);
    if (options.pull) args.push("--pull", options.pull);
    args.push(options.agent ?? "shell");
    if (options.workspace) args.push(options.workspace);
    return this.check(await this.run(args, {}));
  }

  private async createCloud(name: string, options: CreateOptions): Promise<CommandResult> {
    if (options.workspace) {
      throw new SbxError(
        "Cloud sandboxes have no host workspace; omit 'workspace' (download results with downloadFiles()).",
      );
    }
    const shape = resolveCloudShape(options.cpus, options.memory);
    const spec = CLOUD_SHAPES[shape];
    if (spec === undefined) throw new SbxShapeError(`Unknown cloud shape '${shape}'.`);
    const args = ["create", "--name", name, "--cpus", String(spec.cpus), "--memory", `${spec.memoryMib}m`];
    if (options.ttl) args.push("--ttl", options.ttl);
    if (options.onTimeout) args.push("--on-timeout", options.onTimeout);
    args.push(options.agent ?? "shell");
    return this.check(await this.run(args, {}));
  }

  async remove(name: string, options: RemoveOptions = {}): Promise<CommandResult> {
    const args = ["rm"];
    if (options.force ?? true) args.push("--force");
    args.push(name);
    return this.check(await this.run(args, {}));
  }

  async list(): Promise<SandboxInfo[]> {
    const result = await this.check(await this.run(["ls", "--json"], {}));
    return parseSandboxList(result.output);
  }

  async inspect(name: string): Promise<Record<string, unknown> | null> {
    if (this.cloud) {
      throw new SbxError("'sbx inspect' is not supported in cloud mode; use list() for cloud sandbox metadata.");
    }
    const result = await this.run(["inspect", name, "--json"], {});
    if (result.exitCode !== 0) {
      const error = classifyFailure(result);
      if (error instanceof SbxNotFoundError) return null;
      throw error;
    }
    return parseJsonObject(result);
  }

  async exists(name: string): Promise<boolean> {
    const infos = await this.list();
    return infos.some((info) => info.name === name || info.id === name);
  }

  async ttl(sandbox: string): Promise<Record<string, unknown> | null> {
    if (!this.cloud) throw new SbxError("TTL inspection is cloud-only; local sandboxes are not TTL-managed.");
    return parseJsonObject(await this.check(await this.run(["ttl", sandbox, "--json"], {})));
  }

  async extendTtl(sandbox: string, duration: string): Promise<Record<string, unknown> | null> {
    if (!this.cloud) throw new SbxError("TTL extension is cloud-only; local sandboxes are not TTL-managed.");
    const value = duration.startsWith("+") ? duration : `+${duration}`;
    return parseJsonObject(await this.check(await this.run(["ttl", value, sandbox, "--json"], {})));
  }
}

function parseJsonObject(result: CommandResult): Record<string, unknown> | null {
  const payload = result.output.trim();
  if (!payload) return null;
  try {
    const data: unknown = JSON.parse(payload);
    return data !== null && typeof data === "object" && !Array.isArray(data) ? (data as Record<string, unknown>) : null;
  } catch (error) {
    throw new SbxCommandError(`Could not parse sbx JSON output: ${String(error)}`, {
      argv: result.argv,
      exitCode: result.exitCode,
      output: result.output,
    });
  }
}
