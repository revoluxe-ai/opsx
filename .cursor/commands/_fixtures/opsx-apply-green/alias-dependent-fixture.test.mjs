/**
 * Process fixture for /opsx-apply-green alias-loader contracts.
 * Intentional `@/` import so plain `node --test` fails without softProdAliasLoader.
 * Not a product application test.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { NULL_PARALLELE_NOVEL_ID } from "@/api/softProdNovelSave.js";

test("alias fixture loads softProdNovelSave via @/", () => {
  assert.equal(typeof NULL_PARALLELE_NOVEL_ID, "string");
  assert.ok(NULL_PARALLELE_NOVEL_ID.length > 0);
});
