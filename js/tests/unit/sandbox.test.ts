/** Backend unit tests: path safety, partial success, timeouts, lifecycle. */

import { describe, expect, it } from "vitest";

import { SbxTimeoutError } from "../../src/errors.js";
import { SbxSandbox } from "../../src/sandbox.js";

import { SpyTransport, ok } from "../helpers/spy-transport.js";

function sandbox(transport: SpyTransport, options: Record<string, unknown> = {}): SbxSandbox {
  return new SbxSandbox({ name: "demo", transport, autoCreate: false, ...options });
}

describe("path safety", () => {
  it("rejects unsafe upload paths and only uploads safe ones", async () => {
    const transport = new SpyTransport();
    const backend = sandbox(transport);

    const responses = await backend.uploadFiles([
      ["relative.txt", new Uint8Array([1])],
      ["/a/../b.txt", new Uint8Array([1])],
      ["/ok/deep/file.txt", new Uint8Array([1])],
    ]);

    expect(responses.map((response) => response.error)).toEqual(["invalid_path", "invalid_path", null]);
    expect(transport.methods("upload")).toHaveLength(1);
  });

  it("rejects unsafe download paths", async () => {
    const transport = new SpyTransport();
    const backend = sandbox(transport);

    const responses = await backend.downloadFiles(["etc/passwd", "/a/../../etc/passwd"]);

    expect(responses.every((response) => response.error === "invalid_path")).toBe(true);
    expect(transport.methods("download")).toHaveLength(0);
  });
});

describe("partial success", () => {
  it("CT-TR-01 upload partial success", async () => {
    const transport = new SpyTransport({ uploadErrors: [undefined, "permission denied"] });
    const backend = sandbox(transport);

    const responses = await backend.uploadFiles([
      ["/a.txt", new Uint8Array([1])],
      ["/b.txt", new Uint8Array([2])],
    ]);

    expect(responses[0]?.error).toBeNull();
    expect(responses[1]?.error).toBe("permission_denied");
  });

  it("CT-TR-02 download partial success", async () => {
    const transport = new SpyTransport({
      downloadErrors: [undefined, "no such file"],
      downloadContents: [new Uint8Array([1, 2, 3])],
    });
    const backend = sandbox(transport);

    const responses = await backend.downloadFiles(["/a.txt", "/missing.txt"]);

    expect(responses[0]?.content).toEqual(new Uint8Array([1, 2, 3]));
    expect(responses[0]?.error).toBeNull();
    expect(responses[1]?.content).toBeNull();
    expect(responses[1]?.error).toBe("file_not_found");
  });

  it("creates the parent directory before uploading", async () => {
    const transport = new SpyTransport();
    const backend = sandbox(transport);

    await backend.uploadFiles([["/deep/dir/file.txt", new Uint8Array([1])]]);

    const mkdirCalls = transport.methods("exec").filter((call) => String(call.args[1]).includes("mkdir -p"));
    expect(mkdirCalls).toHaveLength(1);
    expect(String(mkdirCalls[0]?.args[1])).toContain("/deep/dir");
  });

  it("stages a world-writable file (sbx cp preserves mode + ownership)", async () => {
    const transport = new SpyTransport();
    const backend = sandbox(transport);

    await backend.uploadFiles([["/ok.txt", new Uint8Array([1])]]);

    expect(transport.methods("upload")[0]?.args[3]).toEqual({ mode: 0o666 });
  });
});

describe("execute", () => {
  it("returns the transport result", async () => {
    const transport = new SpyTransport({ execResults: [ok(["sbx"], "hello")] });
    const backend = sandbox(transport);

    const result = await backend.execute("echo hello");

    expect(result).toMatchObject({ output: "hello", exitCode: 0, truncated: false });
  });

  it("turns a timeout into an error response", async () => {
    const transport = new SpyTransport({ execError: new SbxTimeoutError("host kill", { output: "partial" }) });
    const backend = sandbox(transport, { timeout: 5 });

    const result = await backend.execute("sleep 100");

    expect(result.exitCode).toBeNull();
    expect(result.output).toContain("timed out after 5s");
    expect(result.output).toContain("partial");
  });

  it("forwards the default timeout", async () => {
    const transport = new SpyTransport();
    const backend = sandbox(transport, { timeout: 42 });

    await backend.execute("true");

    expect((transport.methods("exec")[0]?.args[2] as { timeout: number }).timeout).toBe(42);
  });
});

describe("lifecycle", () => {
  it("removes the sandbox only once", async () => {
    const transport = new SpyTransport();
    const backend = sandbox(transport);

    await backend.remove();
    await backend.remove();

    expect(transport.methods("remove")).toHaveLength(1);
  });

  it("close honours autoRemove=false", async () => {
    const transport = new SpyTransport();
    await sandbox(transport, { autoRemove: false }).close();

    expect(transport.methods("remove")).toHaveLength(0);
  });

  it("close removes when autoRemove=true", async () => {
    const transport = new SpyTransport();
    await sandbox(transport, { autoRemove: true }).close();

    expect(transport.methods("remove")).toHaveLength(1);
  });
});

describe("identity and creation", () => {
  it("resolves the stable id from the sandbox list", async () => {
    const transport = new SpyTransport({
      sandboxes: [{ id: "7e733fad", name: "demo", raw: {} }],
    });
    const backend = new SbxSandbox({ name: "demo", transport });

    expect(backend.id).toBe("demo"); // name until first operation resolves it
    await backend.execute("true");
    expect(backend.id).toBe("7e733fad");
  });

  it("reuses an existing sandbox instead of creating it", async () => {
    const transport = new SpyTransport({ sandboxes: [{ id: "x", name: "demo", raw: {} }] });
    await new SbxSandbox({ name: "demo", transport }).execute("true");

    expect(transport.methods("create")).toHaveLength(0);
  });

  it("creates a missing sandbox on first use", async () => {
    const transport = new SpyTransport();
    await new SbxSandbox({ name: "demo", transport }).execute("true");

    const creates = transport.methods("create");
    expect(creates).toHaveLength(1);
    expect((creates[0]?.args[1] as { agent: string }).agent).toBe("shell");
  });

  it("workingDir is the workspace when set", () => {
    const backend = new SbxSandbox({ name: "demo", transport: new SpyTransport(), autoCreate: false, workspace: "/host/p" });
    expect(backend.workingDir).toBe("/host/p");
  });
});
