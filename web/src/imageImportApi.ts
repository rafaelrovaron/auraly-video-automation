import { object } from './api';

export type Association = { variantId: string; path: string };
export type ImagePrepareResult = { operation: 'image_prepare'; manifestPath: string; imagesPath: string; variantCount: number };
export type ImageManifestResult = { operation: 'image_manifest'; manifestPath: string; imagesPath: string; manifestSha256: string; items: Association[] };
export type ImageImportResult = { operation: 'image_import'; mode: 'dry_run' | 'execute'; total: number; created: number; reused: number; approved: number;
  valid: boolean | null; manifestSha256: string | null; validationId: string | null; issues: { code: string; variantId: string | null }[];
  items: { variantId: string; sceneVariantId: string; imageCandidateId: string | null; action: 'create' | 'reuse';
    sha256: string | null; width: number | null; height: number | null; sizeBytes: number | null; format: string | null }[] };
export type ImageOperationView = { jobId: string; campaignId: string; operation: string; status: string; errorCode: string | null;
  result: ImagePrepareResult | ImageManifestResult | ImageImportResult | null };
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0;
const sha = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const id = (value: unknown) => typeof value === 'string' && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value);
const positive = (value: unknown) => Number.isInteger(value) && Number(value) > 0;
const count = (value: unknown) => Number.isInteger(value) && Number(value) >= 0;
export function relativeImagePath(value: unknown): value is string {
  return text(value) && !/^[\\/]|[:\u0000-\u001f]/.test(value)
    && value.replaceAll('\\', '/').split('/').every(part => part !== '' && part !== '.' && part !== '..');
}
export const folderOf = (path: string) => path.slice(0, path.lastIndexOf('/'));
export function imagePrepareResult(value: unknown): value is ImagePrepareResult {
  return object(value) && value.operation === 'image_prepare' && relativeImagePath(value.manifestPath)
    && value.manifestPath.endsWith('/image-import.json') && value.imagesPath === `${folderOf(value.manifestPath)}/images` && positive(value.variantCount);
}
export function imageManifestResult(value: unknown): value is ImageManifestResult {
  return object(value) && value.operation === 'image_manifest' && sha(value.manifestSha256) && relativeImagePath(value.manifestPath)
    && value.manifestPath.endsWith(`/image-import-${value.manifestSha256}.json`) && value.imagesPath === `${folderOf(value.manifestPath)}/images`
    && Array.isArray(value.items) && value.items.length > 0 && value.items.every(item => object(item) && id(item.variantId) && relativeImagePath(item.path))
    && new Set(value.items.map(item => item.variantId)).size === value.items.length;
}
export function imageImportResult(value: unknown): value is ImageImportResult {
  if (!object(value) || value.operation !== 'image_import' || !['dry_run', 'execute'].includes(String(value.mode))
    || !['total', 'created', 'reused', 'approved'].every(key => count(value[key]))
    || !(value.valid === null || typeof value.valid === 'boolean') || !(value.manifestSha256 === null || sha(value.manifestSha256))
    || !(value.validationId === null || text(value.validationId)) || !Array.isArray(value.issues)
    || !value.issues.every(issue => object(issue) && text(issue.code) && (issue.variantId === null || id(issue.variantId)))
    || !Array.isArray(value.items) || !value.items.every(item => object(item) && id(item.variantId) && text(item.sceneVariantId)
      && (item.imageCandidateId === null || text(item.imageCandidateId)) && ['create', 'reuse'].includes(String(item.action))
      && (item.sha256 === null || sha(item.sha256)) && ['width', 'height', 'sizeBytes'].every(key => item[key] === null || positive(item[key]))
      && (item.format === null || text(item.format))) || new Set(value.items.map(item => item.variantId)).size !== value.items.length) return false;
  if (value.mode === 'dry_run') {
    if (value.created !== 0 || value.reused !== 0 || value.approved !== 0) return false;
    if (value.valid === false) return value.items.length === 0 && value.issues.length > 0 && sha(value.manifestSha256) && text(value.validationId);
    if (value.valid === true) return value.total === value.items.length && value.total > 0 && value.issues.length === 0
      && sha(value.manifestSha256) && text(value.validationId) && value.items.every(item => sha(item.sha256)
        && positive(item.width) && positive(item.height) && positive(item.sizeBytes) && text(item.format));
  }
  return value.valid === null && value.issues.length === 0 && value.total === value.items.length;
}
export function imageOperationView(value: unknown): value is ImageOperationView {
  return object(value) && text(value.jobId) && id(value.campaignId) && ['image_prepare', 'image_manifest', 'image_import'].includes(String(value.operation))
    && ['queued', 'running', 'completed', 'failed', 'cancelled', 'retry_scheduled', 'reconciliation_required'].includes(String(value.status))
    && (value.errorCode === null || text(value.errorCode)) && (value.result === null ? value.status !== 'completed'
      : object(value.result) && value.result.operation === value.operation
        && (imagePrepareResult(value.result) || imageManifestResult(value.result) || imageImportResult(value.result)));
}
export function diagnosticLabel(code: string): string {
  const labels: Record<string, string> = {
    image_import_manifest_invalid: 'Manifest inválido.', image_import_campaign_not_found: 'Campanha não encontrada.',
    image_import_variant_coverage_invalid: 'As associações não cobrem todas as cenas.', image_import_source_path_invalid: 'Arquivo ausente ou path inválido.',
    image_import_media_invalid: 'Arquivo não reconhecido como imagem válida.', image_import_orientation_invalid: 'A imagem precisa ser vertical.',
    image_import_approved_candidate_conflict: 'Já existe uma imagem aprovada diferente para esta cena.',
  };
  return Object.hasOwn(labels, code) ? labels[code] : 'Não foi possível validar a imagem. Revise o batch.';
}
