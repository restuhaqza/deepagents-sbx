import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/unit/**/*.test.ts", "tests/contract/**/*.test.ts"],
    testTimeout: 30_000,
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["src/**/*.ts"],
      // src/index.ts is a pure re-export barrel with no executable logic.
      exclude: ["src/index.ts"],
      // Keep the lines/statements floor in sync with Python's
      // `fail_under` in python/pyproject.toml. Branch coverage runs lower,
      // so it gets a looser floor.
      thresholds: {
        lines: 85,
        statements: 85,
        functions: 85,
        branches: 75,
      },
    },
  },
});
