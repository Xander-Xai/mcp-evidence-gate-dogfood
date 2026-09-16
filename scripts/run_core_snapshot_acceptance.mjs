#!/usr/bin/env node

/**
 * Exercise the exact built Core snapshot from an external consumer.
 *
 * The P1 check injects a deterministic reader into Core's snapshot API and
 * proves that one verification attempt consumes one detached byte snapshot.
 * The P2 checks invoke Core's public CLI against consumer-owned fixtures; the
 * script only constructs inputs and asserts the returned model/exit code.
 */

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const PROFILE = "registry-pr-1404@20747d3253ba8638161dd95f1cec70df02993c22";
const NOW = "2026-09-17T00:00:00Z";

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || !process.argv[index + 1]) throw new Error(`missing ${name}`);
  return process.argv[index + 1];
}

function digest(bytes) {
  return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function runCli(cliPath, receiptPath, artifactPath, evidencePath) {
  const args = [
    cliPath,
    "verify",
    "--receipt",
    receiptPath,
    "--artifact",
    artifactPath,
    "--policy",
    "strict-scanner-completeness",
    "--format",
    "json",
    "--now",
    NOW
  ];
  if (evidencePath) args.push("--evidence", evidencePath);
  const result = spawnSync(process.execPath, args, { encoding: "utf8" });
  assert(!result.error, `Core CLI spawn failed: ${result.error ?? "unknown"}`);
  assert(result.stdout.trim().length > 0, `Core CLI emitted no JSON for ${receiptPath}`);
  let model;
  try {
    model = JSON.parse(result.stdout);
  } catch (error) {
    throw new Error(`Core CLI JSON parse failed: ${error}; stdout=${result.stdout}`);
  }
  return { exit_code: result.status, model };
}

function hasReason(model, code) {
  return Array.isArray(model.reason_codes) && model.reason_codes.includes(code);
}

function summarize(result) {
  return {
    exit_code: result.exit_code,
    decision: result.model.decision,
    integrity_status: result.model.integrity_status,
    receipt_status: result.model.receipt_status,
    policy_status: result.model.policy_status,
    admission_status: result.model.admission_status,
    scanner_execution_status: result.model.scanner_execution_status,
    reason_codes: result.model.reason_codes
  };
}

async function main() {
  const coreRoot = resolve(argument("--core-root"));
  const artifactPath = resolve(argument("--artifact"));
  const outputPath = resolve(argument("--out"));
  const coreSha = argument("--core-sha");
  const cliPath = join(coreRoot, "dist", "cli.js");
  const digestModule = await import(pathToFileURL(join(coreRoot, "dist", "core", "digest.js")));
  const scannerModule = await import(pathToFileURL(join(coreRoot, "dist", "core", "scanner-execution.js")));

  const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const fixtureRoot = join(repoRoot, "evidence", "scanner-completeness", "zero-findings-incomplete");
  const baseReceipt = JSON.parse(await readFile(join(fixtureRoot, "receipt.json"), "utf8"));
  const baseEvidence = JSON.parse(await readFile(join(fixtureRoot, "evidence.json"), "utf8"));
  const workRoot = await mkdtemp(join(tmpdir(), "dogfood-core-snapshot-"));

  // P1: Core's injected reader is deliberately prepared to return different
  // bytes on a second call. The verifier must retain the first detached copy.
  const bytesA = Buffer.from(JSON.stringify(baseEvidence, null, 2) + "\n", "utf8");
  const bytesB = Buffer.from(JSON.stringify({ ...baseEvidence, replacement: "B" }, null, 2) + "\n", "utf8");
  let reads = 0;
  const snapshot = await digestModule.readEvidenceSnapshot("p1-evidence.json", async () => {
    reads += 1;
    return reads === 1 ? bytesA : bytesB;
  });
  const binding = digestModule.verifyEvidenceBindingBytes(digestModule.sha256Bytes(bytesA), snapshot);
  const execution = scannerModule.verifyScannerExecutionBytes(snapshot);
  assert(reads === 1, `P1 reader was called ${reads} times`);
  assert(snapshot.snapshot && Buffer.from(snapshot.snapshot.bytes).equals(bytesA), "P1 snapshot bytes changed");
  assert(binding.status === "pass", `P1 binding status was ${binding.status}`);
  assert(execution.reason === "scanner_execution_failed", `P1 scanner result was ${execution.reason}`);
  const p1 = {
    status: "PASS",
    reader_calls: reads,
    binding_status: binding.status,
    scanner_execution_status: execution.reason,
    detached_snapshot: true
  };

  // P2: exercise Core's CLI surface for the four boundary inputs requested by
  // the consumer contract. Each case uses the same artifact and a valid
  // structural receipt, changing only the evidence path/content binding.
  const casesRoot = join(workRoot, "p2");
  await mkdir(casesRoot, { recursive: true });
  const baseReceiptPath = join(casesRoot, "base-receipt.json");
  const baseEvidencePath = join(casesRoot, "base-evidence.json");
  await writeFile(baseReceiptPath, `${JSON.stringify(baseReceipt, null, 2)}\n`, "utf8");
  await writeFile(baseEvidencePath, `${JSON.stringify(baseEvidence, null, 2)}\n`, "utf8");

  const noPath = runCli(cliPath, baseReceiptPath, artifactPath);
  assert(noPath.exit_code === 2, `P2 no-path exit ${noPath.exit_code}`);
  assert(noPath.model.profile === PROFILE, "P2 no-path profile mismatch");
  assert(noPath.model.decision !== "pass", "P2 no-path unexpectedly passed");
  assert(noPath.model.integrity_status === "inconclusive", "P2 no-path integrity was not inconclusive");
  assert(noPath.model.scanner_execution_status === "missing", "P2 no-path scanner status was not missing");
  assert(hasReason(noPath.model, "evidence_binding_required"), "P2 no-path missing evidence_binding_required");
  assert(hasReason(noPath.model, "scanner_execution_missing"), "P2 no-path missing scanner_execution_missing");

  const missingPath = join(casesRoot, "missing-evidence.json");
  const missing = runCli(cliPath, baseReceiptPath, artifactPath, missingPath);
  assert(missing.exit_code === 2, `P2 missing-file exit ${missing.exit_code}`);
  assert(missing.model.integrity_status === "inconclusive", "P2 missing-file integrity was not inconclusive");
  assert(missing.model.scanner_execution_status === "missing", "P2 missing-file scanner status was not missing");
  assert(hasReason(missing.model, "evidence_file_missing"), "P2 missing-file missing evidence_file_missing");
  assert(hasReason(missing.model, "scanner_execution_missing"), "P2 missing-file missing scanner_execution_missing");

  const mismatchEvidencePath = join(casesRoot, "digest-mismatch-evidence.json");
  await writeFile(
    mismatchEvidencePath,
    `${JSON.stringify({ ...baseEvidence, acceptance_marker: "digest-mismatch" }, null, 2)}\n`,
    "utf8"
  );
  const mismatch = runCli(cliPath, baseReceiptPath, artifactPath, mismatchEvidencePath);
  assert(mismatch.exit_code === 2, `P2 digest-mismatch exit ${mismatch.exit_code}`);
  assert(mismatch.model.integrity_status === "inconclusive", "P2 digest-mismatch integrity was not inconclusive");
  assert(mismatch.model.scanner_execution_status === "unverified", "P2 digest-mismatch scanner status was not unverified");
  assert(hasReason(mismatch.model, "evidence_digest_mismatch"), "P2 digest-mismatch missing evidence_digest_mismatch");
  assert(hasReason(mismatch.model, "scanner_execution_unverified"), "P2 digest-mismatch missing scanner_execution_unverified");

  const missingObjectEvidence = clone(baseEvidence);
  delete missingObjectEvidence.scanner_execution;
  const missingObjectBytes = Buffer.from(`${JSON.stringify(missingObjectEvidence, null, 2)}\n`, "utf8");
  const missingObjectEvidencePath = join(casesRoot, "missing-scanner-object-evidence.json");
  await writeFile(missingObjectEvidencePath, missingObjectBytes);
  const missingObjectReceipt = clone(baseReceipt);
  missingObjectReceipt.evidence_digest = digest(missingObjectBytes);
  const missingObjectReceiptPath = join(casesRoot, "missing-scanner-object-receipt.json");
  await writeFile(missingObjectReceiptPath, `${JSON.stringify(missingObjectReceipt, null, 2)}\n`, "utf8");
  const missingObject = runCli(cliPath, missingObjectReceiptPath, artifactPath, missingObjectEvidencePath);
  assert(missingObject.exit_code === 1, `P2 missing-object exit ${missingObject.exit_code}`);
  assert(missingObject.model.integrity_status === "pass", "P2 missing-object integrity was not pass");
  assert(missingObject.model.scanner_execution_status === "missing", "P2 missing-object scanner status was not missing");
  assert(missingObject.model.decision === "fail", "P2 missing-object decision was not fail");
  assert(hasReason(missingObject.model, "scanner_execution_missing"), "P2 missing-object missing scanner_execution_missing");

  const result = {
    schema_version: "dogfood-core-snapshot-acceptance-v1",
    core_sha: coreSha,
    profile: PROFILE,
    p1_toctou: p1,
    p2_cli_surface: {
      no_path: summarize(noPath),
      missing_file: summarize(missing),
      digest_mismatch: summarize(mismatch),
      missing_scanner_object: summarize(missingObject)
    }
  };
  await writeFile(outputPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(result, null, 2));
}

await main();
