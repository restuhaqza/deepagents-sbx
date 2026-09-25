/**
 * Error taxonomy for the Docker Sandboxes (`sbx`) transport.
 *
 * Every error extends {@link SbxError}. Configuration problems the user must
 * fix (missing CLI, missing login, uninitialized policy) get a distinct class
 * so the message can carry an actionable hint.
 */

/** Base class for every Docker Sandboxes transport failure. */
export class SbxError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = new.target.name;
  }
}

/** The `sbx` binary could not be found on `PATH`. */
export class SbxNotInstalledError extends SbxError {}

/** `sbx` is installed but not signed in. */
export class SbxAuthError extends SbxError {}

/** The global sbx network policy has not been initialized. */
export class SbxPolicyError extends SbxError {}

/** The named sandbox does not exist (or was removed concurrently). */
export class SbxNotFoundError extends SbxError {}

/** `sbx` exited non-zero for a command the transport could not interpret. */
export class SbxCommandError extends SbxError {
  readonly argv: readonly string[];
  readonly exitCode: number | null;
  readonly output: string;

  constructor(
    message: string,
    options: { argv?: readonly string[]; exitCode?: number | null; output?: string; cause?: unknown } = {},
  ) {
    super(message, { cause: options.cause });
    this.argv = options.argv ?? [];
    this.exitCode = options.exitCode ?? null;
    this.output = options.output ?? "";
  }
}

/** A transport-level call exceeded its deadline. */
export class SbxTimeoutError extends SbxCommandError {}
