export type Items<T> = { items: T[] };
export type PendingItem = { code: string; stage: string; entityId: string; message: string };
export type CampaignSummary = {
  campaignId: string; character: string; storedStatus: string; sceneCount: number;
  createdAt: string; updatedAt: string; operationalStatus: string; nextPending: PendingItem | null;
};
export type CopyMaster = { copyMasterId: string; version: number; approvalState: string; approvedBy: string | null; sourceText: string; sha256: string; headline: string; hook: string; body: string; cta: string };
export type SceneVariant = { sceneVariantId: string; variantId: string; location: string; action: string; prompt: string; timeAtmosphere: string | null; proofObject: string | null };
export type CampaignDetail = CampaignSummary & { proofObject: string; voicePreset: string; editPreset: string; copyMasters: CopyMaster[]; sceneVariants: SceneVariant[] };
export type SceneStatus = { sceneVariantId: string; variantId: string; currentCopyId: string | null; currentVoiceId: string | null; approvedImageId: string | null; readyRenderIds: string[]; planHashes: string[]; pending: PendingItem[] };
export type CampaignStatus = { campaignId: string; storedStatus: string; operationalStatus: string; nextPending: PendingItem | null; sceneCount: number;
  approvedCopyCount: number; approvedVoiceCount: number; approvedImageCount: number; readyRenderCount: number; planCount: number; scenes: SceneStatus[] };
export type ImageSummary = { imageCandidateId: string; sceneVariantId: string; sourceKind: string; reviewStatus: string; sourcePath: string; sha256: string; width: number; height: number; sizeBytes: number; format: string; rejectionReason: string | null };
export type SceneImages = Items<ImageSummary> & { sceneVariantId: string };
export type VoiceSummary = { voiceMasterId: string; campaignId: string; copyMasterId: string; copyMasterVersion: number; generation: number; provider: string; status: string;
  processedAudioPath: string | null; processedSha256: string | null; durationSeconds: number | null; transcriptMatchStatus: string | null; headlineSpoken: boolean | null;
  qcFindings: string[]; approvedAt: string | null; approvedBy: string | null; approvalReviewReason: string | null; rejectedAt: string | null; rejectedBy: string | null; rejectionReason: string | null };
export type RenderSummary = { renderId: string; sceneVariantId: string; imageCandidateId: string; voiceMasterId: string; jobId: string; status: string; remoteVideoId: string | null; source: { path: string } | null; errorCode: string | null };
export type JobSummary = { jobId: string; jobType: string; campaignId: string | null; sceneVariantId: string | null; status: string; attemptCount: number; maxAttempts: number;
  retrySafety: string; queuedAt: string; startedAt: string | null; completedAt: string | null; nextRetryAt: string | null; lastErrorCode: string | null };
export type OperationView = { jobId: string; campaignId: string; operation: string; status: string; result: Record<string, unknown> | null; errorCode: string | null };
export type WorkerKind = 'local_operations' | 'voice_generate' | 'voice_import' | 'heygen_assets' | 'heygen_videos' | 'editing_render';
export type WorkerState = { state: 'idle' | 'running' | 'stopping'; campaignId: string | null; kind: WorkerKind | null; errorCode: string | null };
export type WorkerObservation = { scope: 'known'; value: WorkerState } | { scope: 'unassociated' };

export function workerState(body: unknown): body is WorkerState {
  return hasFields(body, [], [], ['campaignId', 'kind', 'errorCode'])
    && ['idle', 'running', 'stopping'].includes(String(body.state))
    && (body.kind === null || ['local_operations', 'voice_generate', 'voice_import', 'heygen_assets', 'heygen_videos', 'editing_render'].includes(String(body.kind)));
}

export async function readWorker(id: string, signal: AbortSignal): Promise<WorkerObservation> {
  try {
    const value = await read<WorkerState>(campaignPath(id, '/worker'), signal, workerState);
    return { scope: 'known', value };
  } catch (error) {
    if (error instanceof ApiError && error.status === 404 && error.code === 'not_found') return { scope: 'unassociated' };
    throw error;
  }
}

const messages: Record<string, string> = {
  invalid_request: 'Solicitação inválida.', not_found: 'Recurso não encontrado.',
  artifact_invalid: 'Artefato armazenado inválido.', storage_unavailable: 'Armazenamento local indisponível.',
  internal_error: 'A operação local falhou com segurança.', method_not_allowed: 'Método não permitido.',
  operation_conflict: 'Outro worker ou operação está ocupado.', operation_not_allowed: 'Operação não permitida neste estado.',
  invalid_response: 'Resposta local inválida.', connection_lost: 'Sem conexão com a API local.',
  command_unknown: 'Resultado do comando desconhecido. Atualize o estado antes de decidir uma nova ação.',
};

export class ApiError extends Error {
  constructor(public code: string, public status: number | null = null, public field: string | null = null) {
    super(messages[code] ?? messages.internal_error);
  }
}

export function campaignPath(id: string, suffix = ''): string {
  return `/api/v1/campaigns/${encodeURIComponent(id)}${suffix}`;
}

export function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function hasFields(value: unknown, text: string[], numbers: string[] = [], nullableText: string[] = []): value is Record<string, unknown> {
  return object(value) && text.every(key => typeof value[key] === 'string')
    && numbers.every(key => typeof value[key] === 'number' && Number.isFinite(value[key]))
    && nullableText.every(key => value[key] === null || typeof value[key] === 'string');
}

function arrayOf(value: unknown, accepts: (item: unknown) => boolean): boolean {
  return Array.isArray(value) && value.every(accepts);
}

const stringArray = (value: unknown) => arrayOf(value, item => typeof item === 'string');
const pending = (value: unknown) => hasFields(value, ['code', 'stage', 'entityId', 'message']);

export function collection(value: unknown, accepts?: (item: unknown) => boolean): boolean {
  return object(value) && Array.isArray(value.items) && (!accepts || value.items.every(accepts));
}

export function campaignSummary(value: unknown): boolean {
  return hasFields(value, ['campaignId', 'character', 'operationalStatus'], ['sceneCount'])
    && (value.nextPending === null || pending(value.nextPending));
}

export function campaignDetail(value: unknown): boolean {
  return campaignSummary(value) && object(value)
    && hasFields(value, ['proofObject', 'voicePreset', 'editPreset'])
    && arrayOf(value.copyMasters, copy => hasFields(copy, ['copyMasterId', 'approvalState', 'sourceText', 'sha256', 'headline', 'hook', 'body', 'cta'], ['version'], ['approvedBy'])
      && /^[a-f0-9]{64}$/.test(String(copy.sha256)))
    && arrayOf(value.sceneVariants, scene => hasFields(scene, ['sceneVariantId', 'variantId', 'location', 'action', 'prompt'], [], ['timeAtmosphere', 'proofObject']));
}

export function campaignStatus(value: unknown): boolean {
  return hasFields(value, ['campaignId', 'operationalStatus'], ['sceneCount', 'approvedCopyCount', 'approvedVoiceCount', 'approvedImageCount', 'readyRenderCount', 'planCount'])
    && (value.nextPending === null || pending(value.nextPending))
    && arrayOf(value.scenes, scene => hasFields(scene, ['sceneVariantId'], [], ['currentCopyId', 'currentVoiceId', 'approvedImageId'])
      && stringArray(scene.readyRenderIds) && stringArray(scene.planHashes) && arrayOf(scene.pending, pending));
}

export function sceneImages(value: unknown): boolean {
  return hasFields(value, ['sceneVariantId']) && collection(value, image => hasFields(image,
    ['imageCandidateId', 'sceneVariantId', 'reviewStatus', 'sourceKind', 'sourcePath', 'format', 'sha256'],
    ['width', 'height', 'sizeBytes'], ['rejectionReason']));
}

export function voiceSummary(value: unknown): boolean {
  return hasFields(value, ['voiceMasterId', 'campaignId', 'copyMasterId', 'provider', 'status'], ['copyMasterVersion', 'generation'],
    ['processedAudioPath', 'processedSha256', 'transcriptMatchStatus', 'approvedAt', 'approvedBy', 'approvalReviewReason', 'rejectedAt', 'rejectedBy', 'rejectionReason'])
    && (value.processedSha256 === null || /^[a-f0-9]{64}$/.test(String(value.processedSha256)))
    && (value.durationSeconds === null || (typeof value.durationSeconds === 'number' && Number.isFinite(value.durationSeconds)))
    && (value.headlineSpoken === null || typeof value.headlineSpoken === 'boolean') && stringArray(value.qcFindings);
}

export function renderSummary(value: unknown): boolean {
  return hasFields(value, ['renderId', 'sceneVariantId', 'imageCandidateId', 'voiceMasterId', 'jobId', 'status'], [], ['remoteVideoId', 'errorCode'])
    && (value.source === null || hasFields(value.source, ['path']));
}

export function jobSummary(value: unknown): boolean {
  return hasFields(value, ['jobId', 'jobType', 'status', 'retrySafety', 'queuedAt'], ['attemptCount', 'maxAttempts'],
    ['startedAt', 'completedAt', 'nextRetryAt', 'lastErrorCode']);
}

export function operationView(value: unknown): boolean {
  return hasFields(value, ['jobId', 'operation'], [], ['errorCode']) && (value.result === null || object(value.result));
}

async function responseBody(response: Response): Promise<unknown> {
  let body: unknown;
  try { body = await response.json(); } catch { throw new ApiError('invalid_response', response.status); }
  if (!response.ok) {
    const detail = object(body) && object(body.error) ? body.error : {};
    const code = typeof detail.code === 'string' && Object.hasOwn(messages, detail.code) ? detail.code : 'internal_error';
    const field = typeof detail.field === 'string' && ['campaignId', 'jobId', 'profileId', 'version', 'videoId', 'planHash'].includes(detail.field) ? detail.field : null;
    throw new ApiError(code, response.status, field);
  }
  return body;
}

export async function read<T>(path: string, signal: AbortSignal, accepts?: (value: unknown) => boolean): Promise<T> {
  try {
    const body = await responseBody(await fetch(path, { method: 'GET', signal }));
    if (accepts && !accepts(body)) throw new ApiError('invalid_response');
    return body as T;
  } catch (error) {
    if (signal.aborted || error instanceof ApiError) throw error;
    throw new ApiError('connection_lost');
  }
}

export async function post<T>(path: string, body: object): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    return await responseBody(await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: controller.signal,
    })) as T;
  } catch (error) {
    if (error instanceof ApiError && error.code !== 'invalid_response' && error.code !== 'internal_error'
      && error.status !== null && error.status < 500) throw error;
    throw new ApiError('command_unknown');
  } finally { clearTimeout(timeout); }
}

export function statusLabel(value: string): string {
  const label = ({ needs_input: 'Precisa de informação', needs_review: 'Aguardando aprovação', in_progress: 'Em andamento',
    needs_attention: 'Requer atenção', ready_for_editing: 'Pronto para edição', editing_planned: 'Edição planejada',
    idle: 'Parado', running: 'Em execução', stopping: 'Parando após o trabalho atual',
    queued: 'Na fila', completed: 'Concluído', failed: 'Falhou', cancelled: 'Cancelado',
    retry_scheduled: 'Nova tentativa agendada', reconciliation_required: 'Requer reconciliação',
    approved: 'Aprovado', pending: 'Pendente', rejected: 'Rejeitado', ready: 'Disponível', draft: 'Rascunho',
  } as Record<string, string>)[value];
  return typeof label === 'string' ? label : value;
}

export function pendingLabel(item: PendingItem): string {
  const label = ({ attention_required: 'Atenção necessária', wait_for_job: 'Aguardando Job', editing_plan_missing: 'Plano de edição ausente',
    caption_timing_missing: 'Timing das legendas ausente', renderer_not_implemented: 'Renderer ainda não implementado',
    copy_approval_missing: 'Copy aguarda aprovação', voice_review_required: 'Voz aguarda aprovação', voice_missing: 'Voz ausente',
    image_review_required: 'Imagem aguarda aprovação', image_missing: 'Imagem ausente', heygen_render_missing: 'Render HeyGen ausente',
  } as Record<string, string>)[item.code];
  return typeof label === 'string' ? label : item.code;
}
