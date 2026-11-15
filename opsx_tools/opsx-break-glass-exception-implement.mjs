/**
 * Exception-route downstream implement — delegates to native APPLY_IMPLEMENT path.
 */
import { executeNativeBreakGlassImplement } from "./opsx-break-glass-native-implement.mjs";

export function executeExceptionApplyImplement(opts) {
  return executeNativeBreakGlassImplement(opts);
}
