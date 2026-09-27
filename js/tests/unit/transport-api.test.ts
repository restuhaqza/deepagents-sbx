/** Unit tests for the Docker Sandboxes API transport (UT-API-*). */

import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { Sandboxes } from "@docker/sandboxes";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  SbxAuthError,
  SbxCommandError,
  SbxError,
  SbxNotFoundError,
  SbxShapeError,
  SbxTimeoutError,
} from "../../src/errors.js";
import { SbxSandbox } from "../../src/sandbox.js";
import { ApiSbxTransport, parseDurationMs } from "../../src/transport-api.js";

const tempDirs: string[] = [];

async function tempDir(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "deepagents-sbx-api-"));
  tempDirs.push(dir);
  return dir;
}

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })));
});

interface RunResultShape {
  exitCode: number;
  stdout: string;
  stderr: string;
  incomplete: boolean;
}

interface SandboxCalls {
  run: Array<[unknown, unknown]>;
  write: Array<[unknown, unknown, unknown]>;
  read: unknown[];
  refresh: number;
  delete: Array<[unknown]>;
  waitUntilDeleted: number;
  renewTimeout: Array<[unknown]>;
}

interface FakeSandboxOptions {
  uid?: string;
  name?: string;
  displayName?: string;
  status?: string;
  timeouts?: Record<string, unknown> | null;
  runResult?: Partial<RunResultShape>;
  runError?: unknown;
  writeError?: unknown;
  readError?: unknown;
  content?: Uint8Array;
}

interface FakeSandbox {
  sandbox: Record<string, unknown>;
  calls: SandboxCalls;
}

function makeSandbox(options: FakeSandboxOptions = {}): FakeSandbox {
  const uid = options.uid ?? "sbx_1";
  const calls: SandboxCalls = { run: [], write: [], read: [], refresh: 0, delete: [], waitUntilDeleted: 0, renewTimeout: [] };
  const sandbox: Record<string, unknown> = {
    name: options.name ?? `sandboxes/${uid}`,
    uid,
    displayName: options.displayName ?? "demo",
    status: options.status ?? "running",
    etag: '"v1"',
    core: { agent: "shell", status: options.status ?? "running" },
    effectiveFeatures: options.timeouts === undefined ? undefined : { timeouts: options.timeouts },
    resource: { name: `sandboxes/${uid}` },
    processes: {
      run: async (input: unknown, runOptions: unknown) => {
        calls.run.push([input, runOptions]);
        if (options.runError) throw options.runError;
        return { exitCode: 0, stdout: "", stderr: "", incomplete: false, ...(options.runResult ?? {}) };
      },
    },
    files: {
      write: async (path: unknown, data: unknown, input: unknown) => {
        calls.write.push([path, data, input]);
        if (options.writeError) throw options.writeError;
        return {};
      },
      read: async (path: unknown) => {
        calls.read.push(path);
        if (options.readError) throw options.readError;
        return options.content ?? new Uint8Array();
      },
    },
    refresh: async () => {
      calls.refresh += 1;
      return sandbox;
    },
    delete: async (input: unknown) => {
      calls.delete.push([input]);
      return sandbox;
    },
    waitUntilRunning: async () => sandbox,
    waitUntilDeleted: async () => {
      calls.waitUntilDeleted += 1;
    },
    renewTimeout: async (input: unknown) => {
      calls.renewTimeout.push([input]);
      return sandbox;
    },
  };
  return { sandbox, calls };
}

interface ClientCalls {
  all: number;
  get: unknown[];
  create: unknown[];
  close: number;
}

interface FakeClientOptions {
  sandboxes?: Record<string, unknown>[];
  accepted?: Record<string, unknown>;
  running?: Record<string, unknown>;
  listError?: unknown;
  getError?: unknown;
  createError?: unknown;
}

function makeClient(options: FakeClientOptions = {}): { client: unknown; calls: ClientCalls } {
  const calls: ClientCalls = { all: 0, get: [], create: [], close: 0 };
  const byName = new Map<string, Record<string, unknown>>();
  for (const sandbox of options.sandboxes ?? []) {
    byName.set(String(sandbox.name), sandbox);
    byName.set(String(sandbox.uid), sandbox);
    if (sandbox.displayName) byName.set(String(sandbox.displayName), sandbox);
  }
  const client = {
    all: () => {
      calls.all += 1;
      if (options.listError) throw options.listError;
      const items = options.sandboxes ?? [];
      return (async function* iterate() {
        for (const sandbox of items) yield sandbox;
      })();
    },
    get: async (name: unknown) => {
      calls.get.push(name);
      if (options.getError) throw options.getError;
      const found = byName.get(String(name));
      if (!found) {
        const error = new Error(`No sandbox named '${String(name)}'`);
        error.name = "NotFoundError";
        throw error;
      }
      return found;
    },
    create: async (body: unknown) => {
      calls.create.push(body);
      if (options.createError) throw options.createError;
      const accepted = options.accepted ?? makeSandbox().sandbox;
      return { ...accepted, waitUntilRunning: async () => options.running ?? accepted };
    },
    close: async () => {
      calls.close += 1;
    },
  };
  return { client, calls };
}

function api(client: unknown, overrides: Partial<ConstructorParameters<typeof ApiSbxTransport>[0]> = {}): ApiSbxTransport {
  return new ApiSbxTransport({ client: client as Sandboxes, pollTimeoutMs: 2_000, ...overrides });
}

function sdkError(name: string, code?: string): Error {
  const error = new Error("boom");
  error.name = name;
  if (code) Object.assign(error, { code });
  return error;
}

describe("UT-API exec", () => {
  it("UT-API-01 sends an sh -c argv and merges stdout/stderr", async () => {
    const { sandbox, calls } = makeSandbox({ runResult: { stdout: "out\n", stderr: "err\n", exitCode: 3 } });
    const { client } = makeClient({ sandboxes: [sandbox] });

    const result = await api(client).exec("demo", "echo hi");

    expect(result.exitCode).toBe(3);
    expect(result.output).toBe("out\nerr\n");
    expect(calls.run.at(-1)?.[0]).toEqual({ args: ["sh", "-c", "echo hi"] });
  });

  it("UT-API-02 wraps the command in the remote timeout prefix", async () => {
    const { sandbox, calls } = makeSandbox();
    const { client } = makeClient({ sandboxes: [sandbox] });

    await api(client).exec("demo", "sleep 600", { timeout: 3 });

    expect(calls.run.at(-1)?.[0]).toEqual({ args: ["timeout", "-k", "5s", "3s", "sh", "-c", "sleep 600"] });
    expect(calls.run.at(-1)?.[1]).toMatchObject({ timeoutMs: 9_000 });
  });

  it("UT-API-03 maps exit 124 with the prefix to SbxTimeoutError", async () => {
    const { sandbox } = makeSandbox({ runResult: { exitCode: 124 } });
    const { client } = makeClient({ sandboxes: [sandbox] });

    await expect(api(client).exec("demo", "sleep 600", { timeout: 3 })).rejects.toBeInstanceOf(SbxTimeoutError);
  });

  it("UT-API-04 truncates output at the cap", async () => {
    const { sandbox } = makeSandbox({ runResult: { stdout: "x".repeat(1000) } });
    const { client } = makeClient({ sandboxes: [sandbox] });

    const result = await api(client).exec("demo", "yes", { maxOutputBytes: 10 });

    expect(result.truncated).toBe(true);
    expect(Buffer.byteLength(result.output, "utf8")).toBeLessThanOrEqual(10);
  });

  it("UT-API-05 marks incomplete output as truncated", async () => {
    const { sandbox } = makeSandbox({ runResult: { incomplete: true } });
    const { client } = makeClient({ sandboxes: [sandbox] });

    expect((await api(client).exec("demo", "cmd")).truncated).toBe(true);
  });

  it("UT-API-06 maps an unknown sandbox to SbxNotFoundError", async () => {
    const { client } = makeClient({ sandboxes: [] });

    await expect(api(client).exec("ghost", "ls")).rejects.toBeInstanceOf(SbxNotFoundError);
  });

  it("UT-API-07 translates SDK failures to the SbxError family", async () => {
    const auth = makeSandbox({ runError: sdkError("AuthenticationError", "unauthenticated") });
    await expect(api(makeClient({ sandboxes: [auth.sandbox] }).client).exec("demo", "x")).rejects.toBeInstanceOf(
      SbxAuthError,
    );

    const permission = makeSandbox({ runError: sdkError("PermissionError", "permissionDenied") });
    await expect(
      api(makeClient({ sandboxes: [permission.sandbox] }).client).exec("demo", "x"),
    ).rejects.toBeInstanceOf(SbxCommandError);

    const generic = makeSandbox({ runError: sdkError("ServerError", "internal") });
    await expect(api(makeClient({ sandboxes: [generic.sandbox] }).client).exec("demo", "x")).rejects.toBeInstanceOf(
      SbxCommandError,
    );

    const timeout = makeSandbox({ runError: sdkError("TimeoutError", "deadlineExceeded") });
    await expect(api(makeClient({ sandboxes: [timeout.sandbox] }).client).exec("demo", "x")).rejects.toBeInstanceOf(
      SbxTimeoutError,
    );
  });
});

describe("UT-API files", () => {
  it("UT-API-08 uploads bytes with the source mode", async () => {
    const { sandbox, calls } = makeSandbox();
    const { client } = makeClient({ sandboxes: [sandbox] });
    const dir = await tempDir();
    const local = join(dir, "f.txt");
    await import("node:fs/promises").then((fs) => fs.writeFile(local, "payload"));

    await api(client).upload("demo", local, "/remote/f.txt");

    const [, data, input] = calls.write.at(-1) ?? [];
    expect(Buffer.from(data as Uint8Array).toString("utf8")).toBe("payload");
    expect(input).toMatchObject({ mode: 0o644 });
  });

  it("UT-API-09 downloads bytes to the host", async () => {
    const content = new Uint8Array([1, 2, 3, 4]);
    const { sandbox, calls } = makeSandbox({ content });
    const { client } = makeClient({ sandboxes: [sandbox] });
    const dir = await tempDir();
    const target = join(dir, "out.bin");

    await api(client).download("demo", "/remote/f.txt", target);

    expect(calls.read.at(-1)).toBe("/remote/f.txt");
    const fs = await import("node:fs/promises");
    expect(new Uint8Array(await fs.readFile(target))).toEqual(content);
  });

  it("UT-API-10 maps a missing download to SbxNotFoundError", async () => {
    const { sandbox } = makeSandbox({ readError: sdkError("NotFoundError", "notFound") });
    const { client } = makeClient({ sandboxes: [sandbox] });

    await expect(api(client).download("demo", "/nope", "/tmp/nope")).rejects.toBeInstanceOf(SbxNotFoundError);
  });
});

describe("UT-API create", () => {
  it("UT-API-11 builds the create body and waits for running", async () => {
    const { sandbox } = makeSandbox({ status: "running" });
    const { client, calls } = makeClient({ accepted: { ...sandbox, status: "creating" }, running: sandbox });

    await api(client).create("demo", { cpus: 2, memory: "4g", ttl: "10m" });

    const body = calls.create.at(-1) as Record<string, unknown>;
    expect(body.displayName).toBe("demo");
    expect(body.resources).toEqual({ cpus: 2, memoryMib: "4096" });
    expect(body.imageRef).toBe("docker/sandbox-templates:shell-docker");
    expect(body.lifecycle).toEqual({ timeoutMs: 600_000 });
  });

  it("UT-API-12 rejects a workspace before any request", async () => {
    const { client, calls } = makeClient();

    await expect(api(client).create("demo", { workspace: "/host" })).rejects.toThrow(/no host workspace/);
    expect(calls.create).toHaveLength(0);
  });

  it("UT-API-13 rejects a non-billable shape", async () => {
    const { client, calls } = makeClient();

    await expect(api(client).create("demo", { cpus: 3 })).rejects.toBeInstanceOf(SbxShapeError);
    expect(calls.create).toHaveLength(0);
  });

  it("UT-API-14 rejects an unknown agent without an image", async () => {
    const { client, calls } = makeClient();

    await expect(api(client).create("demo", { agent: "mystery" })).rejects.toThrow(/no known cloud image/);
    expect(calls.create).toHaveLength(0);
  });

  it("UT-API-15 accepts an explicit image ref", async () => {
    const { sandbox } = makeSandbox();
    const { client, calls } = makeClient({ accepted: sandbox, running: sandbox });

    await api(client).create("demo", { agent: "ghcr.io/acme/agent:1" });

    expect((calls.create.at(-1) as Record<string, unknown>).imageRef).toBe("ghcr.io/acme/agent:1");
  });
});

describe("UT-API remove", () => {
  it("UT-API-16 forces deletion and waits until deleted", async () => {
    const { sandbox, calls } = makeSandbox();
    const { client } = makeClient({ sandboxes: [sandbox] });

    await api(client).remove("demo");

    expect(calls.delete.at(-1)?.[0]).toEqual({ force: true });
    expect(calls.waitUntilDeleted).toBe(1);
  });

  it("UT-API-17 maps a missing sandbox to SbxNotFoundError", async () => {
    const { client } = makeClient({ sandboxes: [] });

    await expect(api(client).remove("ghost")).rejects.toBeInstanceOf(SbxNotFoundError);
  });
});

describe("UT-API list / inspect / ttl", () => {
  it("UT-API-18 maps sandbox records", async () => {
    const a = makeSandbox({ uid: "u1", displayName: "a" });
    const b = makeSandbox({ uid: "u2", displayName: "b" });
    const { client } = makeClient({ sandboxes: [a.sandbox, b.sandbox] });

    const infos = await api(client).list();

    expect(infos.map((info) => info.name)).toEqual(["a", "b"]);
    expect(infos.map((info) => info.id)).toEqual(["u1", "u2"]);
  });

  it("UT-API-19 exists reflects the listing", async () => {
    const { sandbox } = makeSandbox({ uid: "u1", displayName: "demo" });
    const found = api(makeClient({ sandboxes: [sandbox] }).client);
    const missing = api(makeClient({ sandboxes: [] }).client);

    expect(await found.exists("demo")).toBe(true);
    expect(await found.exists("u1")).toBe(true);
    expect(await missing.exists("demo")).toBe(false);
  });

  it("UT-API-20 inspect returns a record, or null when absent", async () => {
    const { sandbox } = makeSandbox();
    const { client } = makeClient({ sandboxes: [sandbox] });

    expect(await api(client).inspect("demo")).toMatchObject({ name: "sandboxes/sbx_1", status: "running" });
    expect(await api(makeClient({ sandboxes: [] }).client).inspect("ghost")).toBeNull();
  });

  it("UT-API-21 reads TTL from effective features", async () => {
    const { sandbox } = makeSandbox({ timeouts: { onTimeout: "delete", initialTimeout: "10m" } });
    const { client } = makeClient({ sandboxes: [sandbox] });

    expect(await api(client).ttl("demo")).toEqual({ onTimeout: "delete", initialTimeout: "10m" });
  });

  it("UT-API-22 extends TTL in milliseconds", async () => {
    const { sandbox, calls } = makeSandbox({ timeouts: { initialTimeout: "2h" } });
    const { client } = makeClient({ sandboxes: [sandbox] });

    const result = await api(client).extendTtl("demo", "2h");

    expect(calls.renewTimeout.at(-1)?.[0]).toEqual({ timeoutMs: 7_200_000 });
    expect(result).toEqual({ initialTimeout: "2h" });
  });
});

describe("UT-API helpers / edges", () => {
  it("UT-API-23 parses durations", () => {
    expect(parseDurationMs("90s")).toBe(90_000);
    expect(parseDurationMs("10m")).toBe(600_000);
    expect(parseDurationMs("2h")).toBe(7_200_000);
    expect(parseDurationMs("1.5h")).toBe(5_400_000);
    expect(parseDurationMs("45")).toBe(45_000);
    expect(() => parseDurationMs("soon")).toThrow(SbxError);
  });

  it("UT-API-24 surfaces an actionable error when the SDK is missing", async () => {
    const transport = new ApiSbxTransport({
      loadSdk: async () => {
        throw new Error("module not found");
      },
    });

    await expect(transport.list()).rejects.toThrow(/@docker\/sandboxes/);
  });

  it("UT-API-25 lazily constructs and closes an owned client", async () => {
    const { sandbox } = makeSandbox();
    const { client, calls } = makeClient({ sandboxes: [sandbox] });
    const loader = vi.fn(async () => ({ Sandboxes: class { constructor() { return client as object; } } }));
    const transport = new ApiSbxTransport({
      loadSdk: loader as unknown as () => Promise<typeof import("@docker/sandboxes")>,
    });

    expect(await transport.list()).toHaveLength(1);
    await transport.close();

    expect(loader).toHaveBeenCalledTimes(1);
    expect(calls.close).toBe(1);
  });

  it("UT-API-26 does not close a caller-provided client", async () => {
    const { sandbox } = makeSandbox();
    const { client, calls } = makeClient({ sandboxes: [sandbox] });

    await api(client).close();

    expect(calls.close).toBe(0);
  });

  it("UT-API-27 the backend marks cloud from the transport", () => {
    const { client } = makeClient();
    const backend = new SbxSandbox({ name: "demo", transport: api(client), autoCreate: false });

    expect(backend.cloud).toBe(true);
    expect(() => new SbxSandbox({ name: "demo", transport: api(client), autoCreate: false, workspace: "/host" })).toThrow(
      /no host workspace/,
    );
  });
});
