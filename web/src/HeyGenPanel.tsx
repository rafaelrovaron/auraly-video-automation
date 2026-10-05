import { useEffect, useRef, useState } from 'react';
import { ApiError, campaignPath, post, read } from './api';
import type { CampaignDetail, CampaignStatus, Items, JobSummary, RenderSummary, SceneImages, VoiceSummary } from './api';
import { HEYGEN_DEFAULT_CONFIG, heygenOperationView, heygenSubmission } from './heygenApi';
import type { HeyGenAssetsResult, HeyGenOperationKind, HeyGenOperationView, HeyGenPlanResult, HeyGenSubmitResult } from './heygenApi';
import { usePolling } from './usePolling';
import type { RemoteState } from './usePolling';
import { useUnsavedChanges } from './useUnsavedChanges';

export type HeyGenPanelProps = {campaignId: string; detail: RemoteState<CampaignDetail>; status: RemoteState<CampaignStatus>;
  images: RemoteState<Items<SceneImages>>; voices: RemoteState<Items<VoiceSummary>>; jobs: RemoteState<Items<JobSummary>>; renders: RemoteState<Items<RenderSummary>>};

export function HeyGenPanel(props: HeyGenPanelProps) { return <HeyGenForms key={props.campaignId} {...props} />; }
function HeyGenForms({campaignId, detail, status, images, voices, jobs, renders}: HeyGenPanelProps) {
  const [busy, setBusy] = useState(false), [unknown, setUnknown] = useState(false);
  const [notice, setNotice] = useState(''), [requestId, setRequestId] = useState('');
  const [wrapper, setWrapper] = useState<{jobId: string; kind: HeyGenOperationKind; basis?: string; cap?: number} | null>(null);
  const [assets, setAssets] = useState<(HeyGenAssetsResult & {wrapperId: string}) | null>(null);
  const [cap, setCap] = useState(''), [actor, setActor] = useState(''), [paid, setPaid] = useState(false);
  const [plan, setPlan] = useState<(HeyGenPlanResult & {basis: string; cap: number}) | null>(null);
  const [submitted, setSubmitted] = useState<HeyGenSubmitResult | null>(null);
  const [inspect, setInspect] = useState(''), [inspectKind, setInspectKind] = useState<HeyGenOperationKind>('heygen_assets');
  const handled = useRef(new Set<string>());
  const alive = useRef(true), lock = useRef(false);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useUnsavedChanges(busy || unknown || !!cap || !!actor || paid);
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
  const capNumber = /^\d+$/.test(cap) ? Number(cap) : NaN;
  const validCap = Number.isSafeInteger(capNumber) && capNumber > 0;
  const sceneIds = detail.data?.sceneVariants.map(scene => scene.sceneVariantId) ?? [];
  const material = JSON.stringify([sceneIds, approved.map(voice => [voice.voiceMasterId, voice.copyMasterId, voice.processedAudioPath, voice.processedSha256]),
    images.data?.items.map(scene => [scene.sceneVariantId, scene.items.filter(image => image.reviewStatus === 'approved').map(image => [image.imageCandidateId, image.sourcePath, image.sha256])]),
    renders.data?.items.map(render => render.renderId).sort(), cap]);
  const current = useRef({material, fresh}); current.current = {material, fresh};
  const planUsable = !!plan && plan.basis === material && fresh && plan.cap === capNumber;
  useEffect(() => {if (plan && (!fresh || plan.basis !== material)) {setPlan(null); setPaid(false);}}, [plan, fresh, material]);
  const operation = usePolling<HeyGenOperationView | null>(`${campaignId}:heygen:${wrapper?.jobId ?? 'none'}`,
    signal => wrapper ? read<HeyGenOperationView>(campaignPath(campaignId, `/operations/${encodeURIComponent(wrapper.jobId)}`), signal,
      value => heygenOperationView(value) && value.jobId === wrapper.jobId && value.campaignId === campaignId && value.operation === wrapper.kind
        && (value.result?.operation !== 'heygen_video_plan' || (value.result.sceneVariantIds.length === sceneIds.length
          && value.result.sceneVariantIds.every(id => sceneIds.includes(id))))
        && (value.result?.operation !== 'heygen_video_submit' || value.result.renders.every(render => render.campaignId === campaignId
          && sceneIds.includes(render.sceneVariantId) && images.data?.items.some(scene => scene.items.some(image => image.imageCandidateId === render.imageCandidateId))
          && voices.data?.items.some(voice => voice.voiceMasterId === render.voiceMasterId))))
      : Promise.resolve(null), wrapper ? 2000 : null);
  useEffect(() => {
    const value = operation.data;
    if (!operation.error && value?.status === 'completed' && value.result && !handled.current.has(value.jobId)) {
      handled.current.add(value.jobId);
      if (value.result.operation === 'heygen_assets') setAssets({...value.result, wrapperId: value.jobId});
      if (value.result.operation === 'heygen_video_plan' && wrapper?.basis === current.current.material && current.current.fresh
        && value.result.maxPaidRenders === wrapper.cap) {setPlan({...value.result, basis: wrapper.basis, cap: wrapper.cap!}); setPaid(false);}
      if (value.result.operation === 'heygen_video_submit') {setSubmitted(value.result); setPaid(false); setPlan(null); renders.refresh();}
      jobs.refresh();
    }
  }, [operation.data, operation.error, wrapper, jobs.refresh, renders.refresh]);
  const child = assets?.jobId && jobs.data?.items.find(job => job.jobId === assets.jobId && job.campaignId === campaignId && job.jobType === 'heygen.asset.upload');
  async function command(kind: 'heygen_assets' | 'heygen_video_plan' | 'heygen_video_submit') {
    if (lock.current || blocked || (kind !== 'heygen_assets' && !validCap)
      || (kind === 'heygen_video_submit' && (!planUsable || !paid || !actor.trim() || plan!.reservedCount + plan!.newCount > capNumber))) return;
    lock.current = true; setBusy(true); setNotice('');
    if (kind === 'heygen_video_plan') {setPlan(null); setPaid(false);}
    const id = crypto.randomUUID(); setRequestId(id);
    const basis = material;
    const path = kind === 'heygen_assets' ? '/heygen/assets/prepare' : kind === 'heygen_video_plan' ? '/heygen/videos/plan' : '/heygen/videos/submit';
    const body = {operation: kind, campaignId, requestId: id,
      ...(kind !== 'heygen_assets' ? {config: HEYGEN_DEFAULT_CONFIG, maxPaidRenders: capNumber} : {}),
      ...(kind === 'heygen_video_submit' ? {approvedBy: actor.trim()} : {})};
    try {
      const value = await post<unknown>(campaignPath(campaignId, path), body);
      if (!alive.current) return;
      if (!heygenSubmission(value) || value.campaignId !== campaignId || value.operation !== kind) throw new ApiError('command_unknown');
      setWrapper({jobId: value.jobId, kind: value.operation, basis, cap: capNumber}); setNotice('Operação na fila. Inicie local_operations explicitamente.');
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
    <button disabled={blocked} onClick={() => void command('heygen_assets')}>Preparar assets HeyGen</button>
    <h4>Planejamento e geração</h4><p>Defaults: imagem · 9:16 · 1080p · MP4 · cover · medium. O plano é informativo; o backend recalcula na submissão.</p>
    <label>Limite total de renders reservados da campanha <input inputMode="numeric" value={cap} onChange={event => setCap(event.target.value)} /></label>
    <p>Inclui reservas históricas, mesmo failed ou blocked. Não é valor em moeda.</p>
    <button disabled={blocked || !validCap} onClick={() => void command('heygen_video_plan')}>Planejar batch HeyGen</button>
    {planUsable && <div><p>Novos: {plan!.newCount} · reuso: {plan!.reusedCount} · Reservas históricas: {plan!.reservedCount}</p>
      <p>Total após novas reservas: {plan!.reservedCount + plan!.newCount} · limite: {plan!.maxPaidRenders} · áudio (s): {plan!.totalAudioSeconds}</p></div>}
    <label>Responsável pela geração HeyGen <input value={actor} onChange={event => setActor(event.target.value)} /></label>
    <label><input type="checkbox" checked={paid} disabled={!planUsable || blocked} onChange={event => setPaid(event.target.checked)} />Autorizo a geração paga deste batch; o limite inclui o histórico da campanha.</label>
    <button disabled={blocked || !planUsable || !paid || !actor.trim() || plan!.reservedCount + plan!.newCount > capNumber}
      onClick={() => void command('heygen_video_submit')}>Enfileirar geração HeyGen</button>
    {submitted && <div><p>Reservas aceitas; não significa MP4 concluído. Inicie heygen_videos explicitamente para os Jobs de vídeo da campanha.</p>
      {submitted.renders.map(render => <p key={render.renderId}>Reserva: {render.renderId} · Job: {render.jobId}</p>)}</div>}
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
    <label>Tipo da operação inspecionada <select value={inspectKind} onChange={event => setInspectKind(event.target.value as HeyGenOperationKind)}>
      <option value="heygen_assets">Assets</option><option value="heygen_video_plan">Plano</option><option value="heygen_video_submit">Submissão</option><option value="heygen_reconcile">Reconciliação</option>
    </select></label>
    <button disabled={!inspect || !fresh || busy} onClick={() => {setWrapper({jobId: inspect, kind: inspectKind}); setNotice('Inspeção não comprova identidade ou autoria da intenção.');}}>Inspecionar Job HeyGen</button>
    {unknown && <button disabled={busy} onClick={() => {setUnknown(false); setNotice('Intenção encerrada sem reenvio. Verifique os Jobs antes de criar outra ação.');}}>Encerrar intenção desconhecida sem reenviar</button>}
  </div>;
}
