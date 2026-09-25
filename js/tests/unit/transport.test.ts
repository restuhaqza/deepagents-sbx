/** Unit tests for the CLI transport (UT-CMD-*, UT-EXEC-*, UT-ERR-*, UT-CP-*, UT-CREATE-*). */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  SbxAuthError,
  SbxNotFoundError,
  SbxNotInstalledError,
  SbxPolicyError,
  SbxTimeoutError,
} from "../../src/errors.js";
import { CliSbxTransport, parseSandboxList } from "../../src/transport.js";

import { FakeSbx } from "../helpers/fake-sbx.js";

let fake: FakeSbx;

beforeEach(() => {
  fake = new FakeSbx();
});

afterEach(() => {
  fake.restore();
});

function cli(remoteTimeout = false): CliSbxTransport {
  return new CliSbxTransport("sbx", { remoteTimeout });
}

describe("UT-CMD", () => {
  it("UT-CMD-01 passes the command as an argv array", async () => {
    fake.configure({ stdout: "" });
    await cli().exec("sandbox-1", "ls -la && rm -rf x", { timeout: 0 });

    expect(fake.argvs().at(-1)).toEqual(["exec", "sandbox-1", "sh", "-c", "ls -la && rm -rf x"]);
  });

  it("UT-CMD-02 preserves metacharacters byte-for-byte", async () => {
    const command = `printf '%s' 'a "b" $HOME $(echo x) | ; *' > /tmp/out.txt`;
    fake.configure({ stdout: "" });
    await cli().exec("sandbox-1", command, { timeout: 0 });

    expect(fake.argvs().at(-1)?.[4]).toBe(command);
  });
});

describe("UT-EXEC", () => {
  it("UT-EXEC-01 parses exit code zero", async () => {
    fake.configure({ stdout: "ok", code: 0 });
    const result = await cli().exec("s", "echo ok");

    expect(result.exitCode).toBe(0);
    expect(result.output).toBe("ok");
  });

  it("UT-EXEC-02 captures stdout and stderr", async () => {
    fake.configure({ stdout: "to-stdout\n", stderr: "to-stderr\n", code: 0 });
    const result = await cli().exec("s", "both");

    expect(result.output).toContain("to-stdout");
    expect(result.output).toContain("to-stderr");
  });

  it("UT-EXEC-03 preserves a non-zero exit code", async () => {
    fake.configure({ stdout: "boom", code: 3 });
    const result = await cli().exec("s", "false");

    expect(result.exitCode).toBe(3);
    expect(result.output).toContain("boom");
  });

  it("UT-EXEC-04 sets truncated and respects the cap", async () => {
    fake.configure({ streamMb: 1 });
    const result = await cli().exec("s", "yes", { maxOutputBytes: 1000 });

    expect(result.truncated).toBe(true);
    expect(Buffer.byteLength(result.output, "utf8")).toBeLessThanOrEqual(1000);
  });

  it("UT-EXEC-05 streams and kills instead of buffering", async () => {
    fake.configure({ streamMb: 64, code: 0 });
    const started = Date.now();
    const result = await cli().exec("s", "yes", { maxOutputBytes: 4096 });
    const elapsed = Date.now() - started;

    expect(result.truncated).toBe(true);
    expect(result.output.length).toBeLessThanOrEqual(4096);
    expect(elapsed).toBeLessThan(20_000);
  });

  it("UT-EXEC-06 rejects with SbxTimeoutError when the deadline elapses", async () => {
    fake.configure({ sleep: 30 });
    const started = Date.now();
    await expect(cli().exec("s", "sleep 30", { timeout: 0.5 })).rejects.toBeInstanceOf(SbxTimeoutError);
    expect(Date.now() - started).toBeLessThan(5000);
  });

  it("UT-EXEC-07 wraps with the remote timeout as separate argv", async () => {
    fake.configure({ code: 124, stderr: "timed out" });
    await expect(cli(true).exec("s", "sleep 600", { timeout: 3 })).rejects.toMatchObject({
      exitCode: 124,
    });

    expect(fake.argvs().at(-1)).toEqual(["exec", "s", "timeout", "-k", "5s", "3s", "sh", "-c", "sleep 600"]);
  });
});

describe("UT-ERR", () => {
  it("UT-ERR-01 maps not-authenticated to SbxAuthError with a hint", async () => {
    fake.configure({ stdout: "error: not logged in\n", code: 1 });
    await expect(cli().create("demo")).rejects.toThrow(/Run 'sbx login' first/);
    await expect(cli().create("demo")).rejects.toBeInstanceOf(SbxAuthError);
  });

  it("UT-ERR-02 maps the uninitialized policy to SbxPolicyError", async () => {
    fake.configure({ stdout: "error: global network policy has not been initialized\n", code: 1 });
    await expect(cli().create("demo")).rejects.toBeInstanceOf(SbxPolicyError);
  });

  it("UT-ERR-03 maps a missing sandbox to SbxNotFoundError", async () => {
    fake.configure({ stdout: "error: no such sandbox 'nope'\n", code: 1 });
    await expect(cli().remove("nope")).rejects.toBeInstanceOf(SbxNotFoundError);
  });

  it("UT-ERR-04 raises SbxNotInstalledError for a missing binary", async () => {
    const transport = new CliSbxTransport("definitely-not-a-real-sbx-binary");
    await expect(transport.list()).rejects.toBeInstanceOf(SbxNotInstalledError);
  });
});

describe("UT-CP", () => {
  it("UT-CP-01 upload argv shape", async () => {
    fake.configure({});
    await cli().upload("sandbox-1", "/tmp/stage/file.txt", "/a/b.txt");

    expect(fake.argvs().at(-1)).toEqual(["cp", "/tmp/stage/file.txt", "sandbox-1:/a/b.txt"]);
  });

  it("UT-CP-02 download argv shape", async () => {
    fake.configure({});
    await cli().download("sandbox-1", "/a/b.txt", "/tmp/stage/file.txt");

    expect(fake.argvs().at(-1)).toEqual(["cp", "sandbox-1:/a/b.txt", "/tmp/stage/file.txt"]);
  });
});

describe("UT-CREATE", () => {
  it("UT-CREATE-01 passes sizing and profile flags", async () => {
    fake.configure({});
    await cli().create("demo", { cpus: 4, memory: "8g", profile: "balanced" });

    expect(fake.argvs().at(-1)).toEqual([
      "create",
      "--name",
      "demo",
      "--cpus",
      "4",
      "--memory",
      "8g",
      "--profile",
      "balanced",
      "shell",
    ]);
  });

  it("UT-CREATE-02 omits the workspace path when absent", async () => {
    fake.configure({});
    await cli().create("demo");

    expect(fake.argvs().at(-1)).toEqual(["create", "--name", "demo", "shell"]);
  });

  it("UT-CREATE-03 appends the workspace path when present", async () => {
    fake.configure({});
    await cli().create("demo", { workspace: "/host/project" });

    expect(fake.argvs().at(-1)).toEqual(["create", "--name", "demo", "shell", "/host/project"]);
  });
});

describe("UT-ID", () => {
  it("UT-ID-01 parses the sandbox list and keeps the stable id", () => {
    const infos = parseSandboxList(
      JSON.stringify({ sandboxes: [{ name: "demo", id: "7e733fad", agent: "shell", status: "running" }] }),
    );

    expect(infos).toEqual([
      { id: "7e733fad", name: "demo", agent: "shell", status: "running", raw: expect.anything() },
    ]);
  });

  it("UT-ID-02 falls back to the name when no id is present", () => {
    const infos = parseSandboxList(JSON.stringify({ sandboxes: [{ name: "demo" }] }));

    expect(infos[0]?.id).toBe("demo");
  });
});
