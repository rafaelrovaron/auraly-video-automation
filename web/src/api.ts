export type Items<T> = { items: T[] };
export type PendingItem = { code: string; stage: string; entityId: string; message: string };
export type CampaignSummary = {
  campaignId: string; character: string; storedStatus: string; sceneCount: number;
  createdAt: string; updatedAt: string; operationalStatus: string; nextPending: PendingItem | null;
};

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

export function collection(value: unknown): boolean {
  return object(value) && Array.isArray(value.items);
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
    if (error instanceof ApiError && error.code !== 'invalid_response') throw error;
    throw new ApiError('command_unknown');
  } finally { clearTimeout(timeout); }
}

export function statusLabel(value: string): string {
  return ({ needs_input: 'Precisa de informação', needs_review: 'Aguardando aprovação', in_progress: 'Em andamento',
    needs_attention: 'Requer atenção', ready_for_editing: 'Pronto para edição', editing_planned: 'Edição planejada',
    idle: 'Parado', running: 'Em execução', stopping: 'Parando após o trabalho atual',
    queued: 'Na fila', completed: 'Concluído', failed: 'Falhou', cancelled: 'Cancelado',
    retry_scheduled: 'Nova tentativa agendada', reconciliation_required: 'Requer reconciliação',
    approved: 'Aprovado', pending: 'Pendente', rejected: 'Rejeitado', ready: 'Disponível', draft: 'Rascunho',
  } as Record<string, string>)[value] ?? value;
}

export function pendingLabel(item: PendingItem): string {
  return ({ attention_required: 'Atenção necessária', wait_for_job: 'Aguardando Job', editing_plan_missing: 'Plano de edição ausente',
    caption_timing_missing: 'Timing das legendas ausente', renderer_not_implemented: 'Renderer ainda não implementado',
    copy_approval_missing: 'Copy aguarda aprovação', voice_review_required: 'Voz aguarda aprovação', voice_missing: 'Voz ausente',
    image_review_required: 'Imagem aguarda aprovação', image_missing: 'Imagem ausente', heygen_render_missing: 'Render HeyGen ausente',
  } as Record<string, string>)[item.code] ?? item.code;
}
