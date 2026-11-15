/**
 * Переносимое определение корня репозитория для CLI в opsx_tools.
 *
 * Не привязываемся к глубине вложенности каталога инструмента (корневой
 * ``opsx_tools`` vs устаревший вложенный layout): поднимаемся вверх и ищем маркеры.
 * Явный `--repo` в CLI по-прежнему имеет приоритет (см. parseRepoRootFlag).
 */
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** Относительные пути, достаточные чтобы узнать корень OPSX-репо (достаточно одного). */
const REPO_ROOT_MARKERS = [
  "opsx_tools/registry/apply_design.schema.json",
  "pyproject.toml",
  "openspec/decisions/index.md",
];

const MAX_WALK_DEPTH = 12;

/**
 * Ищет корень репозитория, поднимаясь от `startDir`.
 *
 * @param {string} startDir — абсолютный каталог (обычно каталог .mjs инструмента)
 * @returns {string} абсолютный путь к корню или ближайший разумный fallback
 */
export function discoverRepoRootFromDir(startDir) {
  let dir = resolve(startDir);
  for (let depth = 0; depth < MAX_WALK_DEPTH; depth += 1) {
    for (const marker of REPO_ROOT_MARKERS) {
      if (existsSync(resolve(dir, marker))) {
        return dir;
      }
    }
    const parent = resolve(dir, "..");
    if (parent === dir) {
      break;
    }
    dir = parent;
  }
  return resolve(startDir, "..");
}

/**
 * Корень репо по `import.meta.url` вызывающего CLI-файла (когда `--repo` не передан).
 *
 * @param {string} importMetaUrl
 * @returns {string}
 */
export function defaultRepoRootFromToolFile(importMetaUrl) {
  if (process.env.OPSX_PROJECT_ROOT) return resolve(process.env.OPSX_PROJECT_ROOT);
  const toolDir = dirname(fileURLToPath(importMetaUrl));
  return discoverRepoRootFromDir(toolDir);
}

/**
 * Разбор `--repo <root>`: мутирует копию argv (удаляет флаг).
 *
 * @param {string[]} argv
 * @param {string} importMetaUrl — для fallback без флага
 * @returns {{ repoRoot: string, argv: string[] }}
 */
export function parseRepoRootFlag(argv, importMetaUrl) {
  const args = [...argv];
  const repoFlag = args.indexOf("--repo");
  if (repoFlag >= 0) {
    const repoRoot = resolve(args[repoFlag + 1] ?? "");
    args.splice(repoFlag, 2);
    return { repoRoot, argv: args };
  }
  return {
    repoRoot: defaultRepoRootFromToolFile(importMetaUrl),
    argv: args,
  };
}

/** Explicit distribution/project resource ownership for installed consumers. */
export function resourcePath(repoRoot, ...parts) {
  const relative = parts.join("/").replaceAll("\\", "/");
  const prefixes = ["opsx_tools/", ".cursor/pipeline/", ".cursor/commands/",
    "opsx/shared/", "opsx/schemas/", "opsx/configs/", "opsx/contract_prompts/",
    "opsx/opsx_runtime/", "tools/cursor_executor/", "tools/temporal/"];
  const isCore = prefixes.some(p => (relative.replace(/\/$/, "") + "/").startsWith(p));
  const base = isCore && process.env.OPSX_CORE_ROOT &&
    resolve(repoRoot) === resolve(process.env.OPSX_PROJECT_ROOT || process.cwd())
    ? process.env.OPSX_CORE_ROOT : repoRoot;
  return resolve(base, ...parts);
}
