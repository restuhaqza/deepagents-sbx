/** Cloud transport unit tests (UT-CLOUD-*). No cloud API is contacted. */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SbxError, SbxShapeError } from "../../src/errors.js";
import { SbxSandbox } from "../../src/sandbox.js";
import { CLOUD_SHAPES, CliSbxTransport, parseMemoryMib, resolveCloudShape } from "../../src/transport.js";

import { FakeSbx } from "../helpers/fake-sbx.js";
import { SpyTransport } from "../helpers/spy-transport.js";

let fake: FakeSbx;

beforeEach(() => {
  fake = new FakeSbx();
});

afterEach(() => {
  fake.restore();
});

function cloud(): CliSbxTransport {
  return new CliSbxTransport("sbx", { remoteTimeout: false, cloud: true });
}

describe("UT-CLOUD argv shape", () => {
  it("UT-CLOUD-01 injects --cloud first", async () => {
    fake.configure({ stdout: "" });
    await cloud().exec("s", "echo hi", { timeout: 0 });

    expect(fake.argvs().at(-1)).toEqual(["--cloud", "exec", "s", "sh", "-c", "echo hi"]);
  });

  it("UT-CLOUD-02 create uses shape sizing", async () => {
    fake.configure({});
    await cloud().create("demo", { cpus: 4, memory: "8g" });

    expect(fake.argvs().at(-1)).toEqual([
      "--cloud",
      "create",
      "--name",
      "demo",
      "--cpus",
      "4",
      "--memory",
      "8192m",
      "shell",
    ]);
  });

  it("UT-CLOUD-05 create defaults to the small shape", async () => {
    fake.configure({});
    await cloud().create("demo");

    expect(fake.argvs().at(-1)?.slice(4, 8)).toEqual(["--cpus", "2", "--memory", "4096m"]);
  });

  it("UT-CLOUD-03 rejects an invalid shape before running", async () => {
    fake.configure({});
    await expect(cloud().create("demo", { cpus: 3 })).rejects.toBeInstanceOf(SbxShapeError);

    expect(fake.argvs()).toEqual([]);
  });

  it("UT-CLOUD-04 rejects a workspace", async () => {
    fake.configure({});
    await expect(cloud().create("demo", { workspace: "/host/p" })).rejects.toThrow(/no host workspace/);

    expect(fake.argvs()).toEqual([]);
  });

  it("UT-CLOUD-06 passes ttl and on-timeout", async () => {
    fake.configure({});
    await cloud().create("demo", { ttl: "2h", onTimeout: "stop" });

    const argv = fake.argvs().at(-1) ?? [];
    expect(argv[argv.indexOf("--ttl") + 1]).toBe("2h");
    expect(argv[argv.indexOf("--on-timeout") + 1]).toBe("stop");
  });

  it("UT-CLOUD-07 rejects inspect in cloud mode", async () => {
    fake.configure({});
    await expect(cloud().inspect("demo")).rejects.toThrow(/not supported in cloud mode/);
  });

  it("UT-CLOUD-08 ttl verbs and the cloud-only guard", async () => {
    fake.configure({ stdout: '{"expires_at":"2026-09-25T18:00:00Z"}' });
    await cloud().ttl("demo");
    expect(fake.argvs().at(-1)).toEqual(["--cloud", "ttl", "demo", "--json"]);

    await cloud().extendTtl("demo", "2h");
    expect(fake.argvs().at(-1)).toEqual(["--cloud", "ttl", "+2h", "demo", "--json"]);

    await cloud().extendTtl("demo", "+30m");
    expect(fake.argvs().at(-1)).toEqual(["--cloud", "ttl", "+30m", "demo", "--json"]);

    await expect(new CliSbxTransport("sbx", { remoteTimeout: false }).ttl("demo")).rejects.toBeInstanceOf(SbxError);
  });
});

describe("UT-CLOUD helpers", () => {
  it("UT-CLOUD-09 parseMemoryMib", () => {
    expect(parseMemoryMib("4g")).toBe(4096);
    expect(parseMemoryMib("8192m")).toBe(8192);
    expect(parseMemoryMib("2048")).toBe(2048);
    expect(parseMemoryMib("2GiB")).toBe(2048);
    expect(parseMemoryMib("nonsense")).toBeNull();
  });

  it("UT-CLOUD-09b resolveCloudShape", () => {
    expect(resolveCloudShape()).toBe("small");
    expect(resolveCloudShape(16, "32g")).toBe("xl");
    expect(Object.keys(CLOUD_SHAPES).sort()).toEqual(["large", "medium", "micro", "small", "xl"]);
    expect(() => resolveCloudShape(8, "8g")).toThrow(SbxShapeError);
  });
});

describe("UT-CLOUD backend", () => {
  it("UT-CLOUD-10 rejects a workspace", () => {
    expect(() => new SbxSandbox({ name: "demo", cloud: true, workspace: "/host/p", autoCreate: false })).toThrow(
      /no host workspace/,
    );
  });

  it("UT-CLOUD-11 forwards cloud options and reflects the transport", async () => {
    const transport = new SpyTransport();
    const backend = new SbxSandbox({ name: "demo", transport, cloud: true, ttl: "1h", onTimeout: "stop" });

    await backend.execute("true");

    const create = transport.methods("create")[0];
    expect(create?.args[1]).toMatchObject({ ttl: "1h", onTimeout: "stop" });
    expect(backend.cloud).toBe(true);
  });
});
