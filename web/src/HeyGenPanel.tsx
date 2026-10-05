import { useEffect, useRef, useState } from 'react';
import { ApiError, campaignPath, post, read } from './api';
import type { CampaignDetail, CampaignStatus, Items, JobSummary, RenderSummary, SceneImages, VoiceSummary } from './api';
import { heygenOperationView, heygenSubmission } from './heygenApi';
import type { HeyGenAssetsResult, HeyGenOperationKind, HeyGenOperationView } from './heygenApi';
import { usePolling } from './usePolling';
import type { RemoteState } from './usePolling';
import { useUnsavedChanges } from './useUnsavedChanges';

export type HeyGenPanelProps = {campaignId: string; detail: RemoteState<CampaignDetail>; status: RemoteState<CampaignStatus>;
  images: RemoteState<Items<SceneImages>>; voices: RemoteState<Items<VoiceSummary>>; jobs: RemoteState<Items<JobSummary>>; renders: RemoteState<Items<RenderSummary>>};

export function HeyGenPanel(props: HeyGenPanelProps) { return <HeyGenForms key={props.campaignId} {...props} />; }
function HeyGenForms({campaignId, detail, status, images, voices, jobs, renders}: HeyGenPanelProps) {
  const [busy, setBusy] = useState(false), [unknown, setUnknown] = useState(false);
  const [notice, setNotice] = useState(''), [requestId, setRequestId] = useState('');
  const [wrapper, setWrapper] = useState<{jobId: string; kind: HeyGenOperationKind} | null>(null);
  const [assets, setAssets] = useState<(HeyGenAssetsResult & {wrapperId: string}) | null>(null);
  const [inspect, setInspect] = useState('');
  const alive = useRef(true), lock = useRef(false);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useUnsavedChanges(busy || unknown);
  const snapshots = [detail, status, images, voices, jobs, renders];
  const fresh = snapshots.every(state => state.data && !state.error)
    && detail.data?.campaignId === campaignId && status.data?.campaignId === campaignId
    && voices.data?.items.every(voice => voice.campaignId === campaignId)
    && jobs.data?.items.every(job => job.campaignId === campaignId);
  const approved = voices.data?.items.filter(voice => voice.status === 'approved') ?? [];
  const eligible = approved.length === 1 && !!approved[0].processedSha256 && !!approved[0].processedAudioPath
    && !!detail.data?.sceneVariants.length && detail.data.sceneVariants.every(scene => {
      const candidates = images.data?.items.find(item => item.sceneVariantId === scene.sceneVariantId)?.items;
      return candidates?.filter(image => image.reviewStatus === 'approved' && image.sceneVariantId === scene.sceneVariantId).length === 1;
    });
  const blocked = !fresh || !eligible || busy || unknown;
  const operation = usePolling<HeyGenOperationView | null>(`${campaignId}:heygen:${wrapper?.jobId ?? 'none'}`,
    signal => wrapper ? read<HeyGenOperationView>(campaignPath(campaignId, `/operations/${encodeURIComponent(wrapper.jobId)}`), signal,
      value => heygenOperationView(value) && value.jobId === wrapper.jobId && value.campaignId === campaignId && value.operation === wrapper.kind)
      : Promise.resolve(null), wrapper ? 2000 : null);
  useEffect(() => {
    const value = operation.data;
    if (!operation.error && value?.status === 'completed' && value.result?.operation === 'heygen_assets' && assets?.wrapperId !== value.jobId) {
      setAssets({...value.result, wrapperId: value.jobId}); jobs.refresh();
    }
  }, [operation.data, operation.error, assets?.wrapperId, jobs.refresh]);
  const child = assets?.jobId && jobs.data?.items.find(job => job.jobId === assets.jobId && job.campaignId === campaignId && job.jobType === 'heygen.asset.upload');
  async function prepare() {
    if (lock.current || blocked) return;
    lock.current = true; setBusy(true); setNotice('');
    const id = crypto.randomUUID(); setRequestId(id);
    try {
      const value = await post<unknown>(campaignPath(campaignId, '/heygen/assets/prepare'), {operation: 'heygen_assets', campaignId, requestId: id});
      if (!alive.current) return;
      if (!heygenSubmission(value) || value.campaignId !== campaignId || value.operation !== 'heygen_assets') throw new ApiError('command_unknown');
      setWrapper({jobId: value.jobId, kind: value.operation}); setNotice('Preparação na fila. Inicie local_operations explicitamente.');
    } catch (error) {
      if (!alive.current) return;
      const failure = error instanceof ApiError ? error : new ApiError('command_unknown');
      setUnknown(failure.code === 'command_unknown'); setNotice(failure.message);
    } finally {
      if (alive.current) {jobs.refresh(); setBusy(false); lock.current = false;}
    }
  }
  return <div className="heygen-panel">
    <h3>Operações HeyGen</h3>
    <p>Todas as cenas; imagens e WAV aprovados. OAuth usa a conexão existente da CLI.</p>
    {!fresh && <p role="alert">Dados desatualizados ou indisponíveis. Atualize antes de agir.</p>}
    {!eligible && <p>É necessária uma voz aprovada e uma imagem aprovada por cena.</p>}
    {approved.length === 1 && <p>Áudio aprovado: {approved[0].voiceMasterId} · copy v{approved[0].copyMasterVersion}</p>}
    <button disabled={blocked} onClick={() => void prepare()}>Preparar assets HeyGen</button>
    <p role="status">{notice}</p>{requestId && <p>Intenção: {requestId}</p>}
    {wrapper && <p>Operação: {wrapper.jobId} · {operation.data?.status ?? 'Aguardando leitura'}</p>}
    {operation.error && <p role="alert">Leitura da operação indisponível; fatos anteriores preservados.</p>}
    {operation.data?.errorCode && <p>Erro da operação: {operation.data.errorCode}</p>}
    {assets && <div><p>Uploads planejados: {assets.uploadCount} · reuso: {assets.reusedCount}</p>
      {assets.jobId === null ? <p>Nenhum upload novo enfileirado. Planeje os vídeos para revalidar os assets remotos.</p>
        : <><p>Job de upload: {assets.jobId}</p>
          {!child || jobs.error ? <p>Job filho ainda não comprovado por leitura atual.</p>
            : child.status === 'failed' ? <p>Upload falhou; consulte o Job filho.</p>
              : child.status === 'blocked' ? <p>Upload bloqueado; consulte o Job filho.</p>
                : child.status === 'completed' ? <p>Upload concluído.</p>
                  : <p>Inicie heygen_assets explicitamente para processar o Job de upload.</p>}</>}
    </div>}
    <label>Inspecionar operação HeyGen <select value={inspect} onChange={event => setInspect(event.target.value)}>
      <option value="">Selecione um Job</option>{jobs.data?.items.filter(job => job.jobType === 'api.local.operation').map(job => <option key={job.jobId} value={job.jobId}>{job.jobId}</option>)}
    </select></label>
    <button disabled={!inspect || !fresh || busy} onClick={() => {setWrapper({jobId: inspect, kind: 'heygen_assets'}); setNotice('Inspeção não comprova identidade ou autoria da intenção.');}}>Inspecionar Job HeyGen</button>
    {unknown && <button disabled={busy} onClick={() => {setUnknown(false); setNotice('Intenção encerrada sem reenvio. Verifique os Jobs antes de criar outra ação.');}}>Encerrar intenção desconhecida sem reenviar</button>}
  </div>;
}
