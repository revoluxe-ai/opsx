/**
 * Stage-scoped write surfaces and native tool script paths for Super Implement.
 */
export const NATIVE_TOOL_SCRIPT_BY_COMMAND = Object.freeze({
  "/opsx-apply-design": "opsx_tools/opsx-apply-design.mjs",
  "/opsx-apply-design-review": "opsx_tools/opsx-apply-design-review.mjs",
  "/opsx-apply-scaffold": "opsx_tools/opsx-apply-scaffold.mjs",
  "/opsx-apply-red": "opsx_tools/opsx-apply-red.mjs",
  "/opsx-apply-implement": "opsx_tools/opsx-apply-implement.mjs",
  "/opsx-apply-green": "opsx_tools/opsx-apply-green.mjs",
});

export const STAGE_PERMITTED_WRITE_SURFACES = Object.freeze({
  APPLY_DESIGN: ["apply-design/**"],
  APPLY_DESIGN_REVIEW: ["apply-design-review/**"],
  APPLY_SCAFFOLD: ["apply-scaffold/**"],
  APPLY_RED: ["apply-red/**", "tests/**"],
  APPLY_IMPLEMENT: ["apply-implement/**", "app/**"],
  APPLY_GREEN: ["apply-green/**", "app/**"],
});

/**
 * Hop agent preservation: valid operative receipt + files on disk → no wipe/rewrite.
 * Freshness authority: `evaluateOperativeReceiptForStage` in opsx-super-implement.mjs
 * and helpers in opsx-apply-pipeline-identity.mjs (single signer family).
 */
export const STAGE_RECEIPT_PRESERVATION_POLICY = Object.freeze({
  when: ["operative_receipt_valid", "referenced_files_present"],
  forbid: [
    "wipe",
    "rewrite_from_scratch",
    "regenerate_identical_receipt",
    "invent_receipt_not_visible_to_hop",
  ],
  allow: ["no_change_skip", "in_place_patch_for_validator_errors"],
  after_design_redo:
    "stale_slice_receipts_may_require_digest_patch_not_delete_rewrite",
});

/** Единственная стадия слайса, с которой graph may force APPLY DESIGN redo. */
export const SLICE_DESIGN_REDO_AUTHORIZED_STAGE = "APPLY_GREEN";
