/**
 * Transport that runs commands with the host `sh`, so the real `BaseSandbox`
 * JavaScript helpers execute during contract tests.
 *
 * POSIX-only. Real sbx images are Ubuntu, so this faithfully mirrors the
 * sandbox's `awk`/`find`/`stat`/`grep` behavior on Linux CI.
 */

import { spawn } from "node:child_process";
import { copyFile, mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

import { SbxNotFoundError } from "../../src/errors.js";
import { DEFAULT_MAX_OUTPUT_BYTES } from "../../src/transport.js";
import type { CommandResult, CreateOptions, ExecOptions, RemoveOptions, SandboxInfo, SbxTransport } from "../../src/transport.js";

export class LocalTransport implements SbxTransport {
  readonly calls: { method: string; args: unknown[] }[] = [];

  async exec(sandbox: string, command: string, options: ExecOptions = {}): Promise<CommandResult> {
    this.calls.push({ method: "exec", args: [sandbox, command] });
    const maxOutputBytes = options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES;
    return await new Promise<CommandResult>((resolve) => {
      const child = spawn("sh", ["-c", command], { stdio: ["ignore", "pipe", "pipe"] });
      const chunks: Buffer[] = [];
      let size = 0;
      let truncated = false;
      const collect = (chunk: Buffer): void => {
        const remaining = maxOutputBytes - size;
        if (remaining <= 0) {
          truncated = true;
          return;
        }
        chunks.push(chunk.subarray(0, remaining));
        size += Math.min(chunk.length, remaining);
        if (chunk.length > remaining) truncated = true;
      };
      child.stdout?.on("data", collect);
      child.stderr?.on("data", collect);
      child.on("close", (code) => {
        resolve({ argv: ["sh", "-c", command], output: Buffer.concat(chunks).toString("utf8"), exitCode: code, truncated });
      });
      child.on("error", () => {
        resolve({ argv: ["sh", "-c", command], output: "spawn failed", exitCode: null, truncated: false });
      });
    });
  }

  async upload(sandbox: string, localPath: string, remotePath: string): Promise<CommandResult> {
    this.calls.push({ method: "upload", args: [sandbox, localPath, remotePath] });
    await mkdir(dirname(remotePath), { recursive: true });
    await copyFile(localPath, remotePath);
    return { argv: [], output: "", exitCode: 0, truncated: false };
  }

  async download(sandbox: string, remotePath: string, localPath: string): Promise<CommandResult> {
    this.calls.push({ method: "download", args: [sandbox, remotePath, localPath] });
    try {
      await copyFile(remotePath, localPath);
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code === "ENOENT") throw new SbxNotFoundError(`no such file: ${remotePath}`);
      throw error;
    }
    return { argv: [], output: "", exitCode: 0, truncated: false };
  }

  async create(_name: string, _options: CreateOptions = {}): Promise<CommandResult> {
    return { argv: [], output: "", exitCode: 0, truncated: false };
  }

  async remove(_name: string, _options: RemoveOptions = {}): Promise<CommandResult> {
    return { argv: [], output: "", exitCode: 0, truncated: false };
  }

  async list(): Promise<SandboxInfo[]> {
    return [];
  }

  async inspect(_name: string): Promise<Record<string, unknown> | null> {
    return null;
  }

  async exists(_name: string): Promise<boolean> {
    return false;
  }

  async ttl(_sandbox: string): Promise<Record<string, unknown> | null> {
    return null;
  }

  async extendTtl(_sandbox: string, _duration: string): Promise<Record<string, unknown> | null> {
    return null;
  }
}

/** Write a file directly on the host (helper for contract fixtures). */
export async function writeHostFile(path: string, content: string | Uint8Array): Promise<void> {
  await writeFile(path, content);
}
