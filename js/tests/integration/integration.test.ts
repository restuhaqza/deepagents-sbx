/**
 * Integration tests against a real Docker Sandboxes microVM (opt-in).
 *
 *   npm run test:integration
 *
 * Skipped unless `sbx` is installed, authenticated, and virtualization works.
 */

import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

import { describe, expect, it } from "vitest";

import { SbxSandbox } from "../../src/sandbox.js";

const run = promisify(execFile);

async function sbxReady(): Promise<boolean> {
  try {
    await run("sbx", ["ls", "--json"], { timeout: 60_000 });
    return true;
  } catch {
    return false;
  }
}

const ready = await sbxReady();

function freshSandbox(): SbxSandbox {
  return new SbxSandbox({ name: `dagsbx-js-it-${randomUUID().slice(0, 8)}`, timeout: 60, pull: "missing" });
}

describe.skipIf(!ready)("integration", () => {
  it("IT-ENV-02 POSIX utilities are present", async () => {
    const sandbox = freshSandbox();
    try {
      const names = "sh awk grep find stat sed head tail base64 timeout mkdir cp rm";
      const result = await sandbox.execute(
        `for b in ${names}; do command -v "$b" >/dev/null || { echo missing:$b; exit 1; }; done`,
      );
      expect(result.exitCode).toBe(0);
    } finally {
      await sandbox.remove();
    }
  });

  it("IT-LIFE-01 create, exec, remove", async () => {
    const sandbox = freshSandbox();
    try {
      expect((await sandbox.execute("echo ok")).output.trim()).toBe("ok");
      await sandbox.remove();
      expect(existsSync(join(tmpdir(), "unused"))).toBe(false);
    } finally {
      await sandbox.remove();
    }
  });

  it("IT-EXEC-01 preserves exit code and both streams", async () => {
    const sandbox = freshSandbox();
    try {
      const result = await sandbox.execute("echo to-stdout; echo to-stderr >&2; exit 7");
      expect(result.exitCode).toBe(7);
      expect(result.output).toContain("to-stdout");
      expect(result.output).toContain("to-stderr");
    } finally {
      await sandbox.remove();
    }
  });

  it("IT-EXEC-02 large output is truncated", async () => {
    const sandbox = freshSandbox();
    try {
      const result = await sandbox.execute("yes | head -c 200000000");
      expect(result.truncated).toBe(true);
      expect(Buffer.byteLength(result.output, "utf8")).toBeLessThanOrEqual(sandbox.maxOutputBytes);
    } finally {
      await sandbox.remove();
    }
  });

  it("IT-EXEC-03 a timeout kills the remote process", async () => {
    const sandbox = new SbxSandbox({ name: `dagsbx-js-it-${randomUUID().slice(0, 8)}`, timeout: 3, pull: "missing" });
    try {
      const started = Date.now();
      const result = await sandbox.execute("sleep 600");
      expect(Date.now() - started).toBeLessThan(20_000);
      expect(result.exitCode).toBeNull();
      expect(result.output).toContain("timed out");

      const probe = await sandbox.execute("pgrep -x sleep || true");
      expect(probe.output.replace("pgrep", "")).not.toContain("sleep");
    } finally {
      await sandbox.remove();
    }
  });

  it("IT-FS-02 binary round-trip", async () => {
    const sandbox = freshSandbox();
    try {
      const payload = new Uint8Array(5 * 1024 * 1024);
      for (let index = 0; index < payload.length; index += 1) payload[index] = index % 251;
      const remote = "/home/agent/workspace/js-blob.bin";

      const upload = await sandbox.uploadFiles([[remote, payload]]);
      expect(upload[0]?.error).toBeNull();

      const download = await sandbox.downloadFiles([remote]);
      expect(download[0]?.error).toBeNull();
      expect(download[0]?.content).toEqual(payload);
    } finally {
      await sandbox.remove();
    }
  });

  it("IT-WS-01 workspace bind mount", async () => {
    const workspace = mkdtempSync(join(tmpdir(), "sbx-ws-"));
    writeFileSync(join(workspace, "from-host.txt"), "host-side\n");
    const sandbox = new SbxSandbox({
      name: `dagsbx-js-it-${randomUUID().slice(0, 8)}`,
      workspace,
      timeout: 60,
      pull: "missing",
    });
    try {
      const result = await sandbox.execute(`cat ${workspace}/from-host.txt`);
      expect(result.exitCode).toBe(0);
      expect(result.output).toContain("host-side");

      await sandbox.execute(`echo written-inside > ${workspace}/from-sandbox.txt`);
      expect(readFileSync(join(workspace, "from-sandbox.txt"), "utf8").trim()).toBe("written-inside");
    } finally {
      await sandbox.remove();
      rmSync(workspace, { recursive: true, force: true });
    }
  });

  it("IT-ISO-01 two sandboxes are isolated", async () => {
    const first = freshSandbox();
    const second = freshSandbox();
    try {
      await first.execute("echo first > /home/agent/workspace/shared.txt");
      await second.execute("echo second > /home/agent/workspace/shared.txt");

      expect((await first.execute("cat /home/agent/workspace/shared.txt")).output.trim()).toBe("first");
      expect((await second.execute("cat /home/agent/workspace/shared.txt")).output.trim()).toBe("second");

      const hostnameA = (await first.execute("hostname")).output.trim();
      const hostnameB = (await second.execute("hostname")).output.trim();
      expect(hostnameA).not.toBe(hostnameB);
    } finally {
      await first.remove();
      await second.remove();
    }
  });
});
