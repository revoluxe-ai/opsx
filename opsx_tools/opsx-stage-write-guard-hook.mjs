/**
 * Node --import hook: wraps fs mutations with opsx-stage-write-guard enforcement.
 */
import fs from "node:fs";
import { assertStageWriteAllowed, stageWriteGuardActive } from "./opsx-stage-write-guard.mjs";

function wrapSync(name, original) {
  return function guardedSync(target, ...rest) {
    if (stageWriteGuardActive()) {
      assertStageWriteAllowed(String(target));
    }
    return original.call(fs, target, ...rest);
  };
}

function wrapAsync(name, original) {
  return async function guardedAsync(target, ...rest) {
    if (stageWriteGuardActive()) {
      assertStageWriteAllowed(String(target));
    }
    return original.call(fs, target, ...rest);
  };
}

if (stageWriteGuardActive()) {
  fs.writeFileSync = wrapSync("writeFileSync", fs.writeFileSync);
  fs.appendFileSync = wrapSync("appendFileSync", fs.appendFileSync);
  const origRename = fs.renameSync;
  fs.renameSync = function guardedRenameSync(from, to) {
    if (stageWriteGuardActive()) {
      assertStageWriteAllowed(String(from));
      assertStageWriteAllowed(String(to));
    }
    return origRename.call(fs, from, to);
  };
  fs.mkdirSync = wrapSync("mkdirSync", fs.mkdirSync);
  fs.unlinkSync = wrapSync("unlinkSync", fs.unlinkSync);
  fs.rmSync = wrapSync("rmSync", fs.rmSync);
  fs.copyFileSync = wrapSync("copyFileSync", fs.copyFileSync);
  if (typeof fs.writeFile === "function") {
    fs.writeFile = wrapAsync("writeFile", fs.writeFile);
  }
  if (typeof fs.appendFile === "function") {
    fs.appendFile = wrapAsync("appendFile", fs.appendFile);
  }
}
