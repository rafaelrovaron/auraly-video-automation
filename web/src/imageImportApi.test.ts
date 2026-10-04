import { expect, it } from 'vitest';
import { imageImportResult, imageManifestResult, imageOperationView } from './imageImportApi';

export const manifest = { operation: 'image_manifest', manifestPath: `pipeline/work/inbox/image-import-${'a'.repeat(64)}.json`, imagesPath: 'pipeline/work/inbox/images',
  manifestSha256: 'a'.repeat(64), items: [{ variantId: 'first', path: 'images/a.png' }] };
export const dry = { operation: 'image_import', mode: 'dry_run', total: 1, created: 0, reused: 0, approved: 0, valid: true,
  manifestSha256: 'a'.repeat(64), validationId: 'check-one', issues: [],
  items: [{ variantId: 'first', sceneVariantId: 'scene-one', imageCandidateId: null, action: 'create', sha256: 'b'.repeat(64), width: 360, height: 640, sizeBytes: 500, format: 'png' }] };

it('accepts real metadata and byte-bound validation without allowing incomplete facts', () => {
  expect(imageManifestResult(manifest)).toBe(true); expect(imageImportResult(dry)).toBe(true);
  for (const patch of [{ sha256: null }, { width: 0 }, { format: {} }, { sceneVariantId: null }])
    expect(imageImportResult({ ...dry, items: [{ ...dry.items[0], ...patch }] })).toBe(false);
});
it('rejects escaping paths, mismatched publication digest and duplicate rows', () => {
  expect(imageManifestResult({ ...manifest, manifestPath: '../private.json' })).toBe(false);
  expect(imageManifestResult({ ...manifest, manifestSha256: 'c'.repeat(64) })).toBe(false);
  expect(imageManifestResult({ ...manifest, items: [...manifest.items, manifest.items[0]] })).toBe(false);
});
it('distinguishes invalid validation from successful import and rejects impossible diagnostics', () => {
  const invalid = { ...dry, valid: false, items: [], issues: [{ code: 'image_import_source_path_invalid', variantId: 'first' }] };
  expect(imageImportResult(invalid)).toBe(true);
  expect(imageImportResult({ ...invalid, created: 1 })).toBe(false);
  expect(imageImportResult({ ...invalid, issues: [null] })).toBe(false);
  expect(imageImportResult({ ...dry, items: [...dry.items, dry.items[0]] })).toBe(false);
});
it('rejects wrong operation identities and malformed results before rendering', () => {
  const value = { jobId: 'job', campaignId: 'campaign-one', operation: 'image_manifest', status: 'completed', errorCode: null, result: manifest };
  expect(imageOperationView(value)).toBe(true);
  expect(imageOperationView({ ...value, result: dry })).toBe(false);
  expect(imageOperationView({ ...value, campaignId: {} })).toBe(false);
});
