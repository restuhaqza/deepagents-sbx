/**
 * A fake `sbx` executable installed first on `PATH`.
 *
 * It records every argv it receives (one JSON array per line) and emits canned
 * output selected through environment variables, mirroring the Python test
 * harness.
 */

import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";

const SHIM = `#!/usr/bin/env node
const fs = require("node:fs");
const args = process.argv.slice(2);
const log = process.env.FAKE_SBX_LOG;
if (log) fs.appendFileSync(log, JSON.stringify(args) + "\\n");

const sleep = process.env.FAKE_SBX_SLEEP;
if (sleep) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, Number(sleep) * 1000);
}

const streamMb = process.env.FAKE_SBX_STREAM_MB;
if (streamMb) {
  const chunk = Buffer.alloc(65536, 0x78);
  let remaining = Number(streamMb) * 1024 * 1024;
  while (remaining > 0) {
    fs.writeSync(1, chunk);
    remaining -= chunk.length;
  }
  process.exit(Number(process.env.FAKE_SBX_CODE || "0"));
}

const stdout = process.env.FAKE_SBX_STDOUT || "";
const stderr = process.env.FAKE_SBX_STDERR || "";
if (stdout) fs.writeSync(1, stdout);
if (stderr) fs.writeSync(2, stderr);
process.exit(Number(process.env.FAKE_SBX_CODE || "0"));
`;

export interface FakeSbxConfig {
  stdout?: string;
  stderr?: string;
  code?: number;
  sleep?: number;
  streamMb?: number;
}

export class FakeSbx {
  readonly dir: string;
  readonly log: string;
  readonly shim: string;
  private readonly originalPath: string | undefined;

  constructor() {
    this.dir = mkdtempSync(join(tmpdir(), "fake-sbx-"));
    this.log = join(this.dir, "calls.jsonl");
    this.shim = join(this.dir, "sbx");
    writeFileSync(this.shim, SHIM, { mode: 0o755 });
    this.originalPath = process.env.PATH;
    process.env.PATH = `${this.dir}${delimiter}${this.originalPath ?? ""}`;
    process.env.FAKE_SBX_LOG = this.log;
  }

  configure(config: FakeSbxConfig): void {
    const set = (key: string, value: string | undefined): void => {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    };
    set("FAKE_SBX_STDOUT", config.stdout);
    set("FAKE_SBX_STDERR", config.stderr);
    set("FAKE_SBX_CODE", config.code === undefined ? undefined : String(config.code));
    set("FAKE_SBX_SLEEP", config.sleep === undefined ? undefined : String(config.sleep));
    set("FAKE_SBX_STREAM_MB", config.streamMb === undefined ? undefined : String(config.streamMb));
  }

  argvs(): string[][] {
    try {
      return readFileSync(this.log, "utf8")
        .split("\n")
        .filter((line) => line.length > 0)
        .map((line) => JSON.parse(line) as string[]);
    } catch {
      return [];
    }
  }

  clear(): void {
    rmSync(this.log, { force: true });
  }

  restore(): void {
    process.env.PATH = this.originalPath;
    rmSync(this.dir, { recursive: true, force: true });
  }
}
