/**
 * Register the receipt-CLI loader before the entry module evaluates.
 *
 * `register()` returns a promise. A preload that does not await it finishes
 * while hooks are still pending, so `node --import hook record-cli.mjs`
 * evaluates the record CLI unpatched. Bindings that only name `test_path`
 * then stay `RED_COMMANDS_EMPTY` with no missing-field message.
 * `--import` waits for top-level await in this file before the entry point.
 */
import { register } from "node:module";

await register("./receipt_cli_identity_loader.mjs", import.meta.url);
