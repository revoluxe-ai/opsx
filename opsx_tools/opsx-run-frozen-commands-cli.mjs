#!/usr/bin/env node
/**
 * Запуск списка frozen test-команд (stdout JSON: executions[]).
 * Используется runtime GREEN writer — не подменяет исторический RED envelope.
 */
import { readFileSync } from "node:fs";
import { runFrozenRedCommand } from "./opsx-apply-green.mjs";
import { defaultRepoRootFromToolFile } from "./opsx-repo-root.mjs";

const DEFAULT_ROOT = defaultRepoRootFromToolFile(import.meta.url);

function parseArgs(argv) {
  let repo = DEFAULT_ROOT;
  for (let i = 2; i < argv.length; i += 1) {
    if (argv[i] === "--repo" && argv[i + 1]) {
      repo = argv[i + 1];
      i += 1;
    }
  }
  return { repo };
}

function main() {
  const { repo } = parseArgs(process.argv);
  const raw = readFileSync(0, "utf8");
  const body = JSON.parse(raw || "{}");
  const commands = Array.isArray(body.commands) ? body.commands.map(String) : [];
  const phase = String(body.phase || "green_verify");
  const iteration = Number.isInteger(body.iteration) ? body.iteration : 0;
  const executions = [];
  for (const command of commands) {
    if (!command.trim()) continue;
    const live = runFrozenRedCommand(command, { cwd: repo, timeoutMs: 120000 });
    executions.push({
      phase,
      iteration,
      command,
      exit_code: typeof live.exit_code === "number" ? live.exit_code : 1,
      relevant_output: String(live.relevant_output || "").slice(0, 4000),
      execution_mechanism: live.execution_mechanism || "poetry_pytest",
    });
  }
  process.stdout.write(`${JSON.stringify({ ok: true, executions })}\n`);
}

main();
