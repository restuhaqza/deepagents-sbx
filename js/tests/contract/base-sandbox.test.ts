/**
 * Contract tests: conformance to the deepagents JavaScript `BaseSandbox`.
 *
 * A {@link LocalTransport} runs the real POSIX helper scripts the base class
 * emits (awk/find/stat/grep) against real files, so no Docker or login is
 * needed. IDs mirror the Python suite for parity.
 */

import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { BaseSandbox, isSandboxBackend } from "deepagents";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SbxSandbox } from "../../src/sandbox.js";

import { LocalTransport } from "../helpers/local-transport.js";

let dir: string;
let sandbox: SbxSandbox;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "sbx-contract-"));
  sandbox = new SbxSandbox({ name: "contract", transport: new LocalTransport(), autoCreate: false });
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("CT-EXEC", () => {
  it("CT-EXEC-01 printf round-trip", async () => {
    const result = await sandbox.execute("printf hi");

    expect(result.output).toBe("hi");
    expect(result.exitCode).toBe(0);
  });
});

describe("CT-FS", () => {
  it("CT-FS-01 reads only the requested slice", async () => {
    const path = join(dir, "poem.txt");
    writeFileSync(path, "l1\nl2\nl3\nl4\n");

    const result = await sandbox.read(path, 1, 2);

    expect(result.error).toBeUndefined();
    expect(String(result.content)).toContain("l2");
    expect(String(result.content)).toContain("l3");
    expect(String(result.content)).not.toContain("l1");
    expect(result.startLine).toBe(2);
    expect(result.endLine).toBe(3);
  });

  it("CT-FS-02 read missing surfaces an error", async () => {
    const result = await sandbox.read(join(dir, "nope.txt"));

    expect(result.error).toBeDefined();
  });

  it("CT-FS-03 write creates a file", async () => {
    const path = join(dir, "nested", "created.txt");

    const result = await sandbox.write(path, "hello");

    expect(result.error).toBeUndefined();
    expect(readFileSync(path, "utf8")).toBe("hello");
  });

  it("CT-FS-04 write overwrites an existing file", async () => {
    const path = join(dir, "existing.txt");
    writeFileSync(path, "old");

    const result = await sandbox.write(path, "new");

    expect(result.error).toBeUndefined();
    expect(readFileSync(path, "utf8")).toBe("new");
  });

  it("CT-FS-05 edit replaces a unique occurrence", async () => {
    const path = join(dir, "edit.txt");
    writeFileSync(path, "alpha beta gamma");

    const result = await sandbox.edit(path, "beta", "BETA");

    expect(result.error).toBeUndefined();
    expect(result.occurrences).toBe(1);
    expect(readFileSync(path, "utf8")).toBe("alpha BETA gamma");
  });

  it("CT-FS-06 edit refuses an ambiguous replacement", async () => {
    const path = join(dir, "ambiguous.txt");
    writeFileSync(path, "x x x");

    const result = await sandbox.edit(path, "x", "y", false);

    expect(result.error).toBeDefined();
  });

  it("CT-FS-07 edit replace-all", async () => {
    const path = join(dir, "all.txt");
    writeFileSync(path, "x x x");

    const result = await sandbox.edit(path, "x", "y", true);

    expect(result.error).toBeUndefined();
    expect(result.occurrences).toBe(3);
    expect(readFileSync(path, "utf8")).toBe("y y y");
  });

  it("CT-FS-08 ls lists entries", async () => {
    writeFileSync(join(dir, "file.txt"), "x");
    const { mkdirSync } = await import("node:fs");
    mkdirSync(join(dir, "sub"));

    const result = await sandbox.ls(dir);

    expect(result.error).toBeUndefined();
    // Directories come back with a trailing slash and is_dir: true.
    const byName = new Map((result.files ?? []).map((file) => [file.path.replace(/\/$/, "").split("/").at(-1), file] as const));
    expect(byName.get("sub")?.is_dir).toBe(true);
    expect(byName.get("file.txt")?.is_dir).toBe(false);
  });

  it("CT-FS-09 grep finds matching lines", async () => {
    writeFileSync(join(dir, "a.txt"), "needle here\n");
    writeFileSync(join(dir, "b.txt"), "nothing\n");

    const result = await sandbox.grep("needle", dir);

    expect(result.error).toBeUndefined();
    expect(result.matches).toHaveLength(1);
    expect(result.matches?.[0]?.text).toBe("needle here");
  });

  it("CT-FS-10 glob matches a pattern", async () => {
    const { mkdirSync } = await import("node:fs");
    mkdirSync(join(dir, "pkg"));
    writeFileSync(join(dir, "pkg", "mod.py"), "x");
    writeFileSync(join(dir, "pkg", "readme.md"), "x");

    const result = await sandbox.glob("**/*.py", dir);

    expect(result.error).toBeUndefined();
    // JS glob reports paths relative to the search base, not absolute.
    expect(result.files?.map((file) => file.path)).toEqual(["pkg/mod.py"]);
  });

  it("CT-FS-11 delete then missing", async () => {
    const path = join(dir, "gone.txt");
    writeFileSync(path, "x");

    const first = await sandbox.delete(path);
    const second = await sandbox.delete(path);

    expect(first.error).toBeUndefined();
    expect(second.error).toBeDefined();
  });
});

describe("CT-TR", () => {
  it("CT-TR-03 binary round-trip", async () => {
    const payload = new Uint8Array(5 * 1024 * 1024);
    for (let index = 0; index < payload.length; index += 1) payload[index] = index % 251;
    const remote = join(dir, "blob.bin");

    const upload = await sandbox.uploadFiles([[remote, payload]]);
    expect(upload[0]?.error).toBeNull();

    const download = await sandbox.downloadFiles([remote]);
    expect(download[0]?.error).toBeNull();
    expect(download[0]?.content).toEqual(payload);
  });

  it("CT-TR-04 readRaw returns uncoerced bytes", async () => {
    const path = join(dir, "raw.bin");
    const payload = new Uint8Array([0, 1, 2, 255, 254]);
    writeFileSync(path, payload);

    const result = await sandbox.readRaw(path);

    expect(result.error).toBeUndefined();
    expect(result.data?.content).toBeDefined();
  });

  it("CT-TR-02b download missing is flagged", async () => {
    const result = await sandbox.downloadFiles([join(dir, "not-there.bin")]);

    expect(result[0]?.error).toBe("file_not_found");
    expect(result[0]?.content).toBeNull();
  });
});

describe("CT identity", () => {
  it("CT-ID-01 id is a stable non-empty string", () => {
    expect(typeof sandbox.id).toBe("string");
    expect(sandbox.id.length).toBeGreaterThan(0);
    expect(sandbox.id).toBe(sandbox.id);
  });

  it("CT-TYPE-01 implements the sandbox protocol", () => {
    expect(sandbox).toBeInstanceOf(BaseSandbox);
    expect(sandbox).toBeInstanceOf(SbxSandbox);
    expect(isSandboxBackend(sandbox)).toBe(true);
  });
});
