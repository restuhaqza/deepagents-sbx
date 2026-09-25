/**
 * deepagents-sbx -- Docker Sandboxes (`sbx`) microVM backend for Deep Agents.
 *
 * JavaScript port. {@link SbxSandbox} implements the `deepagents` JavaScript
 * `BaseSandbox` on top of a Docker Sandboxes microVM (its own kernel plus a
 * private Docker daemon).
 */

export {
  SbxAuthError,
  SbxCommandError,
  SbxError,
  SbxNotFoundError,
  SbxNotInstalledError,
  SbxPolicyError,
  SbxTimeoutError,
} from "./errors.js";

export {
  CliSbxTransport,
  DEFAULT_MAX_OUTPUT_BYTES,
  classifyFailure,
  parseSandboxList,
} from "./transport.js";

export type {
  CommandResult,
  CreateOptions,
  ExecOptions,
  RemoveOptions,
  SandboxInfo,
  SbxTransport,
} from "./transport.js";

export { DEFAULT_AGENT, DEFAULT_TIMEOUT, DEFAULT_WORKING_DIR, SbxSandbox } from "./sandbox.js";
export type { SbxSandboxOptions } from "./sandbox.js";

export const VERSION = "0.1.0";
