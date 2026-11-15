/**
 * Разрыв ESM-цикла pipeline-identity ↔ transition-gate.
 * Authoritative R5 verifier регистрируется при загрузке bindings (детерминированно).
 */
import { redOperativeTransitionAuthorized } from "./opsx-apply-red-transition-gate.mjs";

/** @type {((artifact: object, repoRoot?: string) => boolean) | null} */
let redOperativeTransitionAuthorizedImpl = null;

/**
 * Регистрация R5 consumer authority (идемпотентно; transition-gate export — source).
 */
export function registerRedOperativeTransitionAuthorized(fn) {
  redOperativeTransitionAuthorizedImpl = fn;
}

/**
 * Fail-closed, пока gate-модуль не зарегистрирован.
 */
export function getRedOperativeTransitionAuthorized() {
  return redOperativeTransitionAuthorizedImpl;
}

registerRedOperativeTransitionAuthorized(redOperativeTransitionAuthorized);
