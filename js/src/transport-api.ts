/**
 * HTTP transport for Docker Cloud Sandboxes via the official `@docker/sandboxes`
 * SDK.
 *
 * {@link ApiSbxTransport} is the API-backed sibling of {@link CliSbxTransport}:
 * it performs the same `SbxTransport` operations through the SDK instead of
 * spawning the `sbx` CLI, so cloud sandboxes can be driven without a child
 * process.
 *
 * Scope
 * -----
 * The Docker Sandboxes API is **cloud-only** and **experimental**, so this
 * transport sets `cloud = true`. Local sandboxes still require the CLI.
 *
 * The SDK is an **optional peer dependency**: it is loaded lazily only when a
 * client is first needed, so importing `deepagents-sbx` never requires it.
 * Install it with `npm install @docker/sandboxes` to use this transport.
 *
 * Auth is independent of `sbx login`: pass a pre-built client, or `sdkOptions`
 * with an `auth` value from the SDK's `oauth()`/`pat()` helpers.
 */

import { statSync } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";

import type { ClientCreateOptions, ProcessRunOptions, Sandbox, Sandboxes, SandboxesOptions } from "@docker/sandboxes";

import { SbxAuthError, SbxCommandError, SbxError, SbxNotFoundError, SbxShapeError, SbxTimeoutError } from "./errors.js";
import {
  CLOUD_SHAPES,
  DEFAULT_MAX_OUTPUT_BYTES,
  remoteTimeoutPrefix,
  resolveCloudShape,
  type CommandResult,
  type CreateOptions,
  type ExecOptions,
  type RemoveOptions,
  type SandboxInfo,
  type SbxTransport,
} from "./transport.js";

/** Registry image used when `agent === "shell"` (the SDK's bundled kits are optional). */
export const DEFAULT_CLOUD_IMAGE = "docker/sandbox-templates:shell-docker";
/** The optional SDK package this transport loads. */
export const API_SDK_PACKAGE = "@docker/sandboxes";

/** Exit status reported by coreutils `timeout(1)` when it kills a command. */
const TIMEOUT_EXIT_CODE = 124;
const DEFAULT_POLL_TIMEOUT_MS = 300_000;

export interface ApiSbxTransportOptions {
  /**
   * Pre-built SDK client (tests, or a client you already authenticated). Not
   * typed against `@docker/sandboxes` so the optional peer never leaks into
   * this package's public `.d.ts`.
   */
  client?: unknown;
  /** Options forwarded to `new Sandboxes(...)` when `client` is omitted. */
  sdkOptions?: unknown;
  /** Override how the SDK module is loaded (tests). */
  loadSdk?: () => Promise<unknown>;
  /** Registry image used when `agent === "shell"` (default {@link DEFAULT_CLOUD_IMAGE}). */
  image?: string;
  /** Wrap long commands in the sandbox-side `timeout` (default true). */
  remoteTimeout?: boolean;
  /** Seconds the sandbox-side `timeout` waits after SIGTERM before SIGKILL. */
  remoteKillAfter?: number;
  /** Host-side deadline in ms for control-plane SDK calls (default 120000; `0` disables). */
  controlTimeoutMs?: number;
  /** Max ms to wait for a sandbox to start or stop (default 300000). */
  pollTimeoutMs?: number;
}

/** Parse a human duration (`"90s"`, `"10m"`, `"2h"`, `"1.5h"`) to milliseconds. */
export function parseDurationMs(value: string): number {
  const text = value.trim().toLowerCase();
  const match = /^(\d+(?:\.\d+)?)(s|m|h|d)?$/.exec(text);
  if (match === null) {
    throw new SbxError(`Could not parse duration '${value}'; use e.g. '90s', '10m', or '2h'.`);
  }
  const amount = Number(match[1]);
  if (!Number.isFinite(amount)) {
    throw new SbxError(`Could not parse duration '${value}'; use e.g. '90s', '10m', or '2h'.`);
  }
  const unit = match[2] ?? "s";
  const multiplier = { s: 1, m: 60, h: 3600, d: 86400 }[unit] ?? 1;
  return Math.round(amount * multiplier * 1000);
}

function ok(argv: string[]): CommandResult {
  return { argv, output: "", exitCode: 0, truncated: false };
}

function timeoutsOf(features: unknown): Record<string, unknown> | null {
  if (features === null || typeof features !== "object") return null;
  const timeouts = (features as { timeouts?: unknown }).timeouts;
  return timeouts !== null && typeof timeouts === "object" ? (timeouts as Record<string, unknown>) : null;
}

/** Map an SDK failure onto the package's `SbxError` family. */
function translateSdkError(error: unknown, operation: string): unknown {
  if (error instanceof SbxError) return error;
  const candidate = error as { name?: unknown; code?: unknown; httpStatus?: unknown; message?: unknown } | null;
  const name = typeof candidate?.name === "string" ? candidate.name : "";
  const code = typeof candidate?.code === "string" ? candidate.code : "";
  const httpStatus = typeof candidate?.httpStatus === "number" ? candidate.httpStatus : undefined;
  const message = typeof candidate?.message === "string" ? candidate.message : String(error);
  if (name === "NotFoundError" || code === "notFound" || httpStatus === 404) {
    return new SbxNotFoundError(message);
  }
  if (name === "AuthenticationError" || code === "unauthenticated" || httpStatus === 401) {
    return new SbxAuthError(message);
  }
  if (name === "TimeoutError" || code === "deadlineExceeded" || httpStatus === 408 || httpStatus === 504) {
    return new SbxTimeoutError(`${operation} timed out: ${message}`);
  }
  if (name === "PermissionError" || code === "permissionDenied" || httpStatus === 403) {
    return new SbxCommandError(`Permission denied: ${operation}: ${message}`);
  }
  return new SbxCommandError(`${operation} failed: ${message}`, { cause: error });
}

export class ApiSbxTransport implements SbxTransport {
  readonly cloud = true;

  private readonly options: ApiSbxTransportOptions;
  private clientPromise?: Promise<Sandboxes>;
  private clientOwned = false;
  private readonly handles = new Map<string, Sandbox>();
  private readonly resourceNames = new Map<string, string>();

  constructor(options: ApiSbxTransportOptions = {}) {
    this.options = options;
  }

  private get remoteTimeout(): boolean {
    return this.options.remoteTimeout ?? true;
  }

  private get remoteKillAfter(): number {
    return Math.max(1, this.options.remoteKillAfter ?? 5);
  }

  private get pollTimeoutMs(): number {
    return this.options.pollTimeoutMs ?? DEFAULT_POLL_TIMEOUT_MS;
  }

  private callOptions(): { timeoutMs?: number } {
    const value = this.options.controlTimeoutMs ?? 120_000;
    return value > 0 ? { timeoutMs: value } : {};
  }

  private async getClient(): Promise<Sandboxes> {
    this.clientPromise ??= (async () => {
      if (this.options.client) return this.options.client as Sandboxes;
      const loader = this.options.loadSdk ?? (() => import(API_SDK_PACKAGE));
      let sdk: typeof import("@docker/sandboxes");
      try {
        sdk = (await loader()) as typeof import("@docker/sandboxes");
      } catch (error) {
        throw new SbxError(
          `The '${API_SDK_PACKAGE}' SDK is required for the API transport. Install it with \`npm install ${API_SDK_PACKAGE}\`.`,
          { cause: error },
        );
      }
      this.clientOwned = true;
      return new sdk.Sandboxes(this.options.sdkOptions as SandboxesOptions | undefined);
    })();
    return this.clientPromise;
  }

  private async guard<T>(operation: string, action: () => Promise<T>): Promise<T> {
    try {
      return await action();
    } catch (error) {
      throw translateSdkError(error, operation);
    }
  }

  private remember(sandbox: Sandbox): void {
    this.handles.set(sandbox.name, sandbox);
    this.handles.set(sandbox.uid, sandbox);
    this.resourceNames.set(sandbox.uid, sandbox.name);
    if (sandbox.displayName) {
      this.handles.set(sandbox.displayName, sandbox);
      this.resourceNames.set(sandbox.displayName, sandbox.name);
    }
  }

  private async requireHandle(name: string): Promise<Sandbox> {
    const cached = this.handles.get(name);
    if (cached) return cached;
    let resourceName = name.startsWith("sandboxes/") ? name : this.resourceNames.get(name);
    if (resourceName === undefined) {
      await this.list();
      resourceName = this.resourceNames.get(name);
    }
    if (resourceName === undefined) {
      throw new SbxNotFoundError(`No sandbox named '${name}'.`);
    }
    const sandbox = await this.guard("get", async () => (await this.getClient()).get(resourceName ?? name, this.callOptions()));
    this.remember(sandbox);
    this.handles.set(name, sandbox);
    return sandbox;
  }

  private imageFor(agent: string): string {
    const value = agent.trim();
    if (!value || value === "shell") return this.options.image ?? DEFAULT_CLOUD_IMAGE;
    if (value.includes("/") || value.includes(":")) return value;
    throw new SbxError(
      `Agent '${agent}' has no known cloud image. Pass a registry image ref ` +
        `(e.g. '${DEFAULT_CLOUD_IMAGE}') as the agent, or set 'image' on the transport.`,
    );
  }

  /** Release SDK resources if this transport created its own client. */
  async close(): Promise<void> {
    if (this.clientPromise === undefined || !this.clientOwned) return;
    const client = await this.clientPromise.catch(() => undefined);
    await client?.close();
    this.clientPromise = undefined;
    this.clientOwned = false;
  }

  // -- SbxTransport ------------------------------------------------------

  async exec(sandbox: string, command: string, options: ExecOptions = {}): Promise<CommandResult> {
    const timeout = options.timeout !== undefined && options.timeout > 0 ? options.timeout : undefined;
    const handle = await this.requireHandle(sandbox);
    const prefix = remoteTimeoutPrefix(timeout, { enabled: this.remoteTimeout, killAfter: this.remoteKillAfter });
    const args = [...prefix, "sh", "-c", command];
    const maxOutputBytes = options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES;
    const runOptions: ProcessRunOptions = { maxOutputBytes };
    if (timeout !== undefined) {
      runOptions.timeoutMs = (timeout + (prefix.length > 0 ? this.remoteKillAfter + 1 : 0)) * 1000;
    }
    const result = await this.guard("exec", () => handle.processes.run({ args }, runOptions));
    let output = result.stdout + result.stderr;
    let truncated = result.incomplete;
    const encoded = Buffer.from(output, "utf8");
    if (encoded.length > maxOutputBytes) {
      output = encoded.subarray(0, maxOutputBytes).toString("utf8");
      truncated = true;
    }
    const argv = ["api", "exec", handle.name];
    if (result.exitCode === TIMEOUT_EXIT_CODE && prefix.length > 0) {
      throw new SbxTimeoutError(`Command timed out after ${timeout}s inside sandbox '${sandbox}'.`, {
        argv,
        exitCode: TIMEOUT_EXIT_CODE,
        output,
      });
    }
    return { argv, output, exitCode: result.exitCode, truncated };
  }

  async upload(sandbox: string, localPath: string, remotePath: string): Promise<CommandResult> {
    const handle = await this.requireHandle(sandbox);
    return this.guard("upload", async () => {
      const data = new Uint8Array(await readFile(localPath));
      const mode = statSync(localPath).mode & 0o777;
      await handle.files.write(remotePath, data, { mode }, this.callOptions());
      return ok(["api", "upload", handle.name, remotePath]);
    });
  }

  async download(sandbox: string, remotePath: string, localPath: string): Promise<CommandResult> {
    const handle = await this.requireHandle(sandbox);
    return this.guard("download", async () => {
      const data = await handle.files.read(remotePath, {}, this.callOptions());
      await writeFile(localPath, data);
      return ok(["api", "download", handle.name, remotePath]);
    });
  }

  async create(name: string, options: CreateOptions = {}): Promise<CommandResult> {
    if (options.workspace) {
      throw new SbxError(
        "Cloud sandboxes have no host workspace; pass workspace=undefined and use downloadFiles() instead.",
      );
    }
    const shape = resolveCloudShape(options.cpus, options.memory);
    const spec = CLOUD_SHAPES[shape];
    if (spec === undefined) throw new SbxShapeError(`Unknown cloud shape '${shape}'.`);
    const lifecycle: { timeoutMs: number; onTimeout?: string } = { timeoutMs: 0 };
    if (options.ttl !== undefined) {
      lifecycle.timeoutMs = parseDurationMs(options.ttl);
      if (options.onTimeout !== undefined) lifecycle.onTimeout = options.onTimeout;
    }
    const body = {
      displayName: name,
      resources: { cpus: spec.cpus, memoryMib: String(spec.memoryMib) },
      imageRef: this.imageFor(options.agent ?? "shell"),
      ...(options.pull === "always" ? { pullPolicy: "always" as const } : {}),
      ...(options.ttl !== undefined ? { lifecycle } : {}),
    };
    const sandbox = await this.guard("create", async () => {
      const accepted = await (await this.getClient()).create(body as ClientCreateOptions, this.callOptions());
      return accepted.waitUntilRunning({ timeoutMs: this.pollTimeoutMs });
    });
    this.remember(sandbox);
    this.handles.set(name, sandbox);
    this.resourceNames.set(name, sandbox.name);
    return ok(["api", "create", name]);
  }

  async remove(name: string, options: RemoveOptions = {}): Promise<CommandResult> {
    const handle = await this.requireHandle(name);
    const force = options.force ?? true;
    return this.guard("remove", async () => {
      const latest = await handle.refresh(this.callOptions());
      const deleting = await latest.delete({ force }, this.callOptions());
      await deleting?.waitUntilDeleted({ timeoutMs: this.pollTimeoutMs });
      this.handles.delete(name);
      return ok(["api", "rm", name]);
    });
  }

  async list(): Promise<SandboxInfo[]> {
    return this.guard("list", async () => {
      const client = await this.getClient();
      const infos: SandboxInfo[] = [];
      for await (const sandbox of client.all({}, this.callOptions())) {
        this.remember(sandbox);
        infos.push({
          id: sandbox.uid,
          name: sandbox.displayName || sandbox.name,
          status: sandbox.status || undefined,
          agent: sandbox.core?.agent || undefined,
          raw: sandbox.resource as unknown as Record<string, unknown>,
        });
      }
      return infos;
    });
  }

  async inspect(name: string): Promise<Record<string, unknown> | null> {
    let handle: Sandbox;
    try {
      handle = await this.requireHandle(name);
    } catch (error) {
      if (error instanceof SbxNotFoundError) return null;
      throw error;
    }
    return { name: handle.name, displayName: handle.displayName, status: handle.status, etag: handle.etag };
  }

  async exists(name: string): Promise<boolean> {
    const infos = await this.list();
    return infos.some((info) => info.name === name || info.id === name);
  }

  async ttl(sandbox: string): Promise<Record<string, unknown> | null> {
    const handle = await this.requireHandle(sandbox);
    return this.guard("ttl", async () => {
      const latest = await handle.refresh(this.callOptions());
      return timeoutsOf(latest.effectiveFeatures);
    });
  }

  async extendTtl(sandbox: string, duration: string): Promise<Record<string, unknown> | null> {
    const handle = await this.requireHandle(sandbox);
    const timeoutMs = parseDurationMs(duration);
    return this.guard("extendTtl", async () => {
      const updated = await handle.renewTimeout({ timeoutMs }, this.callOptions());
      return timeoutsOf(updated.effectiveFeatures);
    });
  }
}
