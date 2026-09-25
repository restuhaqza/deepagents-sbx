/** Recording test double for {@link SbxTransport}. */

import { statSync } from "node:fs";
import { writeFile } from "node:fs/promises";

import { SbxCommandError } from "../../src/errors.js";
import type { CommandResult, CreateOptions, ExecOptions, RemoveOptions, SandboxInfo, SbxTransport } from "../../src/transport.js";

export interface Call {
  method: string;
  args: unknown[];
}

export function ok(argv: string[] = ["sbx"], output = ""): CommandResult {
  return { argv, output, exitCode: 0, truncated: false };
}

export interface SpyTransportOptions {
  sandboxes?: SandboxInfo[];
  execResults?: CommandResult[];
  uploadErrors?: (string | undefined)[];
  downloadErrors?: (string | undefined)[];
  downloadContents?: (Uint8Array | undefined)[];
  execError?: Error;
}

export class SpyTransport implements SbxTransport {
  readonly calls: Call[] = [];
  private readonly sandboxes: SandboxInfo[];
  private readonly execResults: CommandResult[];
  private readonly uploadErrors: (string | undefined)[];
  private readonly downloadErrors: (string | undefined)[];
  private readonly downloadContents: (Uint8Array | undefined)[];
  private readonly execError?: Error;

  constructor(options: SpyTransportOptions = {}) {
    this.sandboxes = options.sandboxes ?? [];
    this.execResults = options.execResults ?? [];
    this.uploadErrors = options.uploadErrors ?? [];
    this.downloadErrors = options.downloadErrors ?? [];
    this.downloadContents = options.downloadContents ?? [];
    this.execError = options.execError;
  }

  methods(name: string): Call[] {
    return this.calls.filter((call) => call.method === name);
  }

  async exec(sandbox: string, command: string, options: ExecOptions = {}): Promise<CommandResult> {
    this.calls.push({ method: "exec", args: [sandbox, command, options] });
    if (this.execError) throw this.execError;
    return this.execResults.shift() ?? ok();
  }

  async upload(sandbox: string, localPath: string, remotePath: string): Promise<CommandResult> {
    const mode = statSync(localPath).mode & 0o777;
    this.calls.push({ method: "upload", args: [sandbox, localPath, remotePath, { mode }] });
    const error = this.uploadErrors.shift();
    if (error !== undefined) throw new SbxCommandError(error);
    return ok();
  }

  async download(sandbox: string, remotePath: string, localPath: string): Promise<CommandResult> {
    this.calls.push({ method: "download", args: [sandbox, remotePath, localPath] });
    const error = this.downloadErrors.shift();
    if (error !== undefined) throw new SbxCommandError(error);
    const content = this.downloadContents.shift();
    await writeFile(localPath, content ?? new Uint8Array());
    return ok();
  }

  async create(name: string, options: CreateOptions = {}): Promise<CommandResult> {
    this.calls.push({ method: "create", args: [name, options] });
    return ok();
  }

  async remove(name: string, options: RemoveOptions = {}): Promise<CommandResult> {
    this.calls.push({ method: "remove", args: [name, options] });
    return ok();
  }

  async list(): Promise<SandboxInfo[]> {
    this.calls.push({ method: "list", args: [] });
    return [...this.sandboxes];
  }

  async inspect(name: string): Promise<Record<string, unknown> | null> {
    this.calls.push({ method: "inspect", args: [name] });
    return null;
  }

  async exists(name: string): Promise<boolean> {
    this.calls.push({ method: "exists", args: [name] });
    return this.sandboxes.some((info) => info.name === name || info.id === name);
  }
}
