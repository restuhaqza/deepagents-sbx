/**
 * Billable Docker Cloud Sandboxes integration tests (opt-in).
 *
 * Creates **real cloud sandboxes that cost money**. Only runs with
 * `SBX_CLOUD_INTEGRATION=1`; each sandbox has a short TTL and is removed on
 * teardown.
 *
 *   SBX_CLOUD_INTEGRATION=1 npm run test:integration
 */

import { randomBytes } from "node:crypto";

import { describe, expect, it } from "vitest";

import { SbxSandbox } from "../../src/sandbox.js";

const enabled = process.env.SBX_CLOUD_INTEGRATION === "1";

function name(): string {
  return `dagsbx-cloud-${randomBytes(3).toString("hex")}`;
}

function cloudSandbox(autoRemove = true): SbxSandbox {
  return new SbxSandbox({
    name: name(),
    cloud: true,
    cpus: 1,
    memory: "2g",
    ttl: "10m",
    onTimeout: "delete",
    timeout: 60,
    autoRemove,
  });
}

describe.skipIf(!enabled)("cloud integration", () => {
  it("IT-CLOUD-01 lifecycle", async () => {
    const sandbox = cloudSandbox();
    try {
      expect(sandbox.cloud).toBe(true);
      const result = await sandbox.execute("echo cloud-ok");
      expect(result.exitCode).toBe(0);
      expect(result.output).toContain("cloud-ok");

      const infos = await (sandbox as unknown as { transport: { list(): Promise<{ name: string }[]> } }).transport.list();
      expect(infos.map((info) => info.name)).toContain(sandbox.name);
    } finally {
      await sandbox.remove();
    }
  });

  it("IT-CLOUD-03 binary round-trip", async () => {
    const sandbox = cloudSandbox();
    try {
      const payload = new Uint8Array(randomBytes(1024 * 1024));
      const remote = "/home/agent/workspace/cloud-blob.bin";
      const upload = await sandbox.uploadFiles([[remote, payload]]);
      expect(upload[0]?.error).toBeNull();

      const download = await sandbox.downloadFiles([remote]);
      expect(download[0]?.error).toBeNull();
      expect(download[0]?.content).toEqual(payload);
    } finally {
      await sandbox.remove();
    }
  });

  it("IT-CLOUD-04 TTL is readable and extendable", async () => {
    const sandbox = cloudSandbox();
    try {
      expect(await sandbox.ttl()).toBeTruthy();
      expect(await sandbox.extendTtl("5m")).toBeTruthy();
    } finally {
      await sandbox.remove();
    }
  });

  it("IT-CLOUD-06 rejects a workspace", () => {
    expect(() => new SbxSandbox({ name: name(), cloud: true, workspace: "/tmp/project", autoCreate: false })).toThrow(
      /no host workspace/,
    );
  });
});
