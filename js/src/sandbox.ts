/**
 * deepagents sandbox backend backed by a Docker Sandboxes microVM.
 *
 * {@link SbxSandbox} implements the four members the JavaScript `BaseSandbox`
 * requires -- `execute`, `uploadFiles`, `downloadFiles`, and `id`. Every other
 * operation (`read`, `write`, `edit`, `delete`, `ls`, `grep`, `glob`) is derived
 * by the base class and funnelled through `execute()`.
 *
 * The JS base class is pure POSIX (awk/find/stat), so unlike the Python port it
 * needs no `python3` inside the sandbox.
 */

import { randomUUID } from "node:crypto";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, posix } from "node:path";

import { BaseSandbox } from "deepagents";
import type { ExecuteResponse, FileDownloadResponse, FileOperationError, FileUploadResponse } from "deepagents";

import { SbxNotFoundError, SbxTimeoutError } from "./errors.js";
import { CliSbxTransport, DEFAULT_MAX_OUTPUT_BYTES, classifyFailure, type SbxTransport } from "./transport.js";

export const DEFAULT_AGENT = "shell";
/** Directory `sbx exec` starts in for a workspace-less `shell` sandbox. */
export const DEFAULT_WORKING_DIR = "/home/agent/workspace";
export const DEFAULT_TIMEOUT = 120;

/**
 * Permissions for staged uploads.
 *
 * `sbx cp` preserves the source file's mode *and* ownership, and the host uid is
 * not the sandbox user's uid. A `0600` staging file therefore lands unreadable
 * (and un-editable by `edit`) inside the VM. `0666` makes it usable regardless
 * of the uid mapping.
 */
const UPLOAD_MODE = 0o666;

export interface SbxSandboxOptions {
  /** Sandbox handle. Auto-generated when omitted. */
  name?: string;
  /** Built-in sbx agent image (default `shell`). */
  agent?: string;
  /** Host dir bind-mounted at the same absolute path inside the VM. */
  workspace?: string;
  cpus?: number;
  memory?: string;
  profile?: string;
  /** Transport implementation (default {@link CliSbxTransport}). */
  transport?: SbxTransport;
  /** Default command timeout in seconds; `0`/`undefined` disables it. */
  timeout?: number;
  /** Output cap; the child process is killed once it is reached. */
  maxOutputBytes?: number;
  /** Delete the sandbox on `close()` (default `true`). */
  autoRemove?: boolean;
  /** Create the sandbox on first use if missing (default `true`). */
  autoCreate?: boolean;
  /** Image pull policy passed to `sbx create`. */
  pull?: string;
}

function isSafeAbsolutePath(path: string): boolean {
  if (!path || !path.startsWith("/")) return false;
  return !path.split("/").includes("..");
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

function errorCode(error: unknown): FileOperationError {
  const text = error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase();
  if (text.includes("permission denied")) return "permission_denied";
  if (text.includes("is a directory")) return "is_directory";
  if (error instanceof SbxNotFoundError || text.includes("no such file") || text.includes("not found")) {
    return "file_not_found";
  }
  return "permission_denied";
}

export class SbxSandbox extends BaseSandbox {
  readonly name: string;
  readonly agent: string;
  readonly workspace?: string;
  readonly timeout: number;
  readonly maxOutputBytes: number;
  readonly autoRemove: boolean;

  private readonly autoCreate: boolean;
  private readonly pull?: string;
  private readonly cpus?: number;
  private readonly memory?: string;
  private readonly profile?: string;
  private readonly transport: SbxTransport;

  private idValue?: string;
  private ready?: Promise<void>;
  private tempDirPromise?: Promise<string>;
  private removed = false;

  constructor(options: SbxSandboxOptions = {}) {
    super();
    this.transport = options.transport ?? new CliSbxTransport();
    this.name = options.name ?? `deepagents-sbx-${randomUUID().slice(0, 8)}`;
    this.agent = options.agent ?? DEFAULT_AGENT;
    this.workspace = options.workspace;
    this.timeout = options.timeout ?? DEFAULT_TIMEOUT;
    this.maxOutputBytes = options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES;
    this.autoRemove = options.autoRemove ?? true;
    this.autoCreate = options.autoCreate ?? true;
    this.pull = options.pull;
    this.cpus = options.cpus;
    this.memory = options.memory;
    this.profile = options.profile;
  }

  /** Stable sandbox id once resolved; the name until then. */
  get id(): string {
    return this.idValue ?? this.name;
  }

  /** Directory new commands start in. */
  get workingDir(): string {
    return this.workspace ?? DEFAULT_WORKING_DIR;
  }

  // -- construction helpers ---------------------------------------------

  /** Attach to an existing sandbox without creating it. */
  static async attach(name: string, options: SbxSandboxOptions = {}): Promise<SbxSandbox> {
    const sandbox = new SbxSandbox({ ...options, name, autoCreate: false });
    if (!(await sandbox.transport.exists(name))) {
      throw new SbxNotFoundError(`No sandbox named '${name}'`);
    }
    return sandbox;
  }

  /** Construct and eagerly create the sandbox. */
  static async create(options: SbxSandboxOptions = {}): Promise<SbxSandbox> {
    const sandbox = new SbxSandbox(options);
    await sandbox.ensureReady();
    return sandbox;
  }

  private ensureReady(): Promise<void> {
    this.ready ??= this.start();
    return this.ready;
  }

  private async start(): Promise<void> {
    if (this.autoCreate && !(await this.transport.exists(this.name))) {
      await this.transport.create(this.name, {
        agent: this.agent,
        workspace: this.workspace,
        cpus: this.cpus,
        memory: this.memory,
        profile: this.profile,
        pull: this.pull,
      });
    }
    this.idValue = await this.resolveId();
  }

  private async resolveId(): Promise<string> {
    try {
      const infos = await this.transport.list();
      return infos.find((info) => info.name === this.name || info.id === this.name)?.id ?? this.name;
    } catch {
      return this.name;
    }
  }

  // -- BaseSandbox required interface ------------------------------------

  override async execute(command: string): Promise<ExecuteResponse> {
    await this.ensureReady();
    const timeout = this.timeout > 0 ? this.timeout : undefined;
    try {
      const result = await this.transport.exec(this.name, command, {
        timeout,
        maxOutputBytes: this.maxOutputBytes,
      });
      return { output: result.output, exitCode: result.exitCode, truncated: result.truncated };
    } catch (error) {
      if (error instanceof SbxTimeoutError) {
        const suffix = error.output ? `\n${error.output}` : "";
        return { output: `Error: command timed out after ${this.timeout}s.${suffix}`, exitCode: null, truncated: false };
      }
      throw error;
    }
  }

  override async uploadFiles(files: Array<[string, Uint8Array]>): Promise<FileUploadResponse[]> {
    await this.ensureReady();
    const responses: FileUploadResponse[] = [];
    for (const [path, data] of files) {
      if (!isSafeAbsolutePath(path)) {
        responses.push({ path, error: "invalid_path" });
        continue;
      }
      const staged = join(await this.tempDirPath(), `upload-${randomUUID()}`);
      try {
        await writeFile(staged, data, { mode: UPLOAD_MODE });
        await chmod(staged, UPLOAD_MODE); // writeFile's mode is masked by umask
        const parent = posix.dirname(path);
        if (parent && parent !== "/") await this.ensureDir(parent);
        await this.transport.upload(this.name, staged, path);
        responses.push({ path, error: null });
      } catch (error) {
        responses.push({ path, error: errorCode(error) });
      } finally {
        await rm(staged, { force: true });
      }
    }
    return responses;
  }

  override async downloadFiles(paths: string[]): Promise<FileDownloadResponse[]> {
    await this.ensureReady();
    const responses: FileDownloadResponse[] = [];
    for (const path of paths) {
      if (!isSafeAbsolutePath(path)) {
        responses.push({ path, content: null, error: "invalid_path" });
        continue;
      }
      const staged = join(await this.tempDirPath(), `download-${randomUUID()}`);
      try {
        await this.transport.download(this.name, path, staged);
        const content = new Uint8Array(await readFile(staged));
        responses.push({ path, content, error: null });
      } catch (error) {
        responses.push({ path, content: null, error: errorCode(error) });
      } finally {
        await rm(staged, { force: true });
      }
    }
    return responses;
  }

  // -- lifecycle ---------------------------------------------------------

  private tempDirPath(): Promise<string> {
    this.tempDirPromise ??= mkdtemp(join(tmpdir(), "deepagents-sbx-"));
    return this.tempDirPromise;
  }

  private async ensureDir(remoteDir: string): Promise<void> {
    const result = await this.transport.exec(this.name, `mkdir -p ${shellQuote(remoteDir)}`, {
      timeout: this.timeout > 0 ? this.timeout : undefined,
      maxOutputBytes: this.maxOutputBytes,
    });
    if (result.exitCode !== 0) throw classifyFailure(result);
  }

  /**
   * Remove the sandbox and its resources (idempotent).
   *
   * Deliberately not named `delete`: `BaseSandbox.delete(filePath)` reserves
   * that name for the file-deletion tool.
   */
  async remove(): Promise<void> {
    if (this.removed) return;
    try {
      await this.transport.remove(this.name, { force: true });
    } catch (error) {
      if (!(error instanceof SbxNotFoundError)) throw error;
    }
    this.removed = true;
    await this.cleanupTempDir();
  }

  /** Delete the sandbox when `autoRemove` is set. */
  async close(): Promise<void> {
    if (this.autoRemove) await this.remove();
  }

  private async cleanupTempDir(): Promise<void> {
    if (!this.tempDirPromise) return;
    const dir = await this.tempDirPromise.catch(() => undefined);
    if (dir) await rm(dir, { recursive: true, force: true });
  }
}
