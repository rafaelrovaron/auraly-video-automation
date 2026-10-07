import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ApiError, campaignDetail, campaignPath, campaignStatus, campaignSummary, collection, jobSummary, object, operationView, pendingLabel, read, readWorker, sceneImages, statusLabel, voiceSummary } from './api';
import type { CampaignDetail, CampaignStatus, CampaignSummary, Items, JobSummary, OperationView, SceneImages, VoiceSummary } from './api';
import { heygenRenderView } from './heygenApi';
import type { HeyGenRenderView } from './heygenApi';
import { usePolling } from './usePolling';
import type { RemoteState } from './usePolling';
import { WorkerControls } from './WorkerControls';
import { CampaignCreateForm, CopyVersionForm } from './CampaignForms';
import { ImageImportPanel } from './ImageImportPanel';
import { VoicePanel } from './VoicePanel';
import { HeyGenPanel } from './HeyGenPanel';
import { EditingPanel } from './EditingPanel';

function Updated({ state }: { state: RemoteState<unknown> }) {
  return <>
    {state.error && <p role="alert">{state.error.message} ({state.error.code}) {state.data ? 'Dados desatualizados.' : 'Seção indisponível.'}</p>}
    {state.loading && !state.data && <p role="status">Carregando…</p>}
    {state.lastSuccessAt !== null && <small>Última leitura: {new Date(state.lastSuccessAt).toLocaleString('pt-BR')}</small>}
  </>;
}

function Section({ title, state, children }: { title: string; state: RemoteState<unknown>; children: ReactNode }) {
  return <section aria-label={title}><h2>{title}</h2><Updated state={state} />{state.data !== null && children}</section>;
}

function Facts({ entries }: { entries: [string, ReactNode][] }) {
  return <dl>{entries.map(([label, value]) => <div className="fact" key={label}><dt>{label}</dt><dd>{value ?? 'Não disponível'}</dd></div>)}</dl>;
}

function ErrorCode({ code }: { code: string | null }) {
  return code ? <p role="status">Erro: {code}. {/oauth|auth|credential|account/i.test(code) && 'Conecte o HeyGen pelo fluxo OAuth existente da CLI.'}</p> : null;
}

export function CampaignList() {
  const state = usePolling('campaign-list', signal => read<Items<CampaignSummary>>('/api/v1/campaigns', signal, value => collection(value, campaignSummary)), 5000);
  return <>
    <div className="section-heading"><h1>Campanhas</h1><button onClick={state.refresh}>Atualizar</button></div>
    <p>Consulte o andamento da produção. Nenhuma ação paga é iniciada ao abrir o painel.</p><Updated state={state} />
    {state.data?.items.length === 0 && <p>Nenhuma campanha. Use Criar campanha abaixo.</p>}
    <details><summary>Criar campanha</summary><CampaignCreateForm onCreated={campaignId => { window.location.hash = `#/campaigns/${encodeURIComponent(campaignId)}`; }} /></details>
    <div className="campaign-list">{state.data?.items.map(item => <article key={item.campaignId}>
      <h2><a href={`#/campaigns/${encodeURIComponent(item.campaignId)}`}>{item.campaignId}</a></h2>
      <p>{item.character}</p><p>{item.sceneCount} cenas · {statusLabel(item.operationalStatus)}</p>
      <p>{item.nextPending ? pendingLabel(item.nextPending) : 'Sem pendência informada'}</p>
    </article>)}</div>
  </>;
}

export function CampaignDetailPanel({ campaignId }: { campaignId: string }) {
  const detail = usePolling(campaignPath(campaignId), signal => read<CampaignDetail>(campaignPath(campaignId), signal,
    value => campaignDetail(value) && object(value) && value.campaignId === campaignId
      && new Set((value.sceneVariants as CampaignDetail['sceneVariants']).map(scene => scene.sceneVariantId)).size === (value.sceneVariants as unknown[]).length), null);
  const status = usePolling(campaignPath(campaignId, '/status'), signal => read<CampaignStatus>(campaignPath(campaignId, '/status'), signal,
    value => campaignStatus(value) && object(value) && value.campaignId === campaignId), 2000);
  const jobs = usePolling(campaignPath(campaignId, '/jobs'), signal => read<Items<JobSummary>>(campaignPath(campaignId, '/jobs'), signal,
    value => collection(value, item => jobSummary(item) && object(item) && item.campaignId === campaignId)
      && object(value) && new Set((value.items as JobSummary[]).map(job => job.jobId)).size === (value.items as JobSummary[]).length), 2000);
  const worker = usePolling(campaignPath(campaignId, '/worker'), signal => readWorker(campaignId, signal), 2000);
  const images = usePolling(campaignPath(campaignId, '/images'), signal => read<Items<SceneImages>>(campaignPath(campaignId, '/images'), signal,
    value => collection(value, sceneImages) && object(value)
      && new Set((value.items as SceneImages[]).map(scene => scene.sceneVariantId)).size === (value.items as SceneImages[]).length
      && (value.items as SceneImages[]).every(scene => scene.items.every(image => image.sceneVariantId === scene.sceneVariantId))
      && new Set((value.items as SceneImages[]).flatMap(scene => scene.items.map(image => image.imageCandidateId))).size === (value.items as SceneImages[]).flatMap(scene => scene.items).length), null);
  const voices = usePolling(campaignPath(campaignId, '/voices'), signal => read<Items<VoiceSummary>>(campaignPath(campaignId, '/voices'), signal,
    value => collection(value, item => voiceSummary(item) && object(item) && item.campaignId === campaignId)
      && object(value) && new Set((value.items as VoiceSummary[]).map(voice => voice.voiceMasterId)).size === (value.items as VoiceSummary[]).length), null);
  const sceneIds = detail.data?.sceneVariants.map(scene => scene.sceneVariantId);
  const renders = usePolling(`${campaignPath(campaignId, '/heygen/renders')}:${JSON.stringify(sceneIds)}`, signal => {
    if (!sceneIds) return Promise.reject(new ApiError('invalid_response'));
    return read<Items<HeyGenRenderView>>(campaignPath(campaignId, '/heygen/renders'), signal,
      value => collection(value, item => heygenRenderView(item) && item.campaignId === campaignId && sceneIds.includes(item.sceneVariantId))
        && object(value) && new Set((value.items as HeyGenRenderView[]).map(render => render.renderId)).size === (value.items as HeyGenRenderView[]).length);
  }, null);
  const [selectedJob, setSelectedJob] = useState<string | null>(null);
  const previousFingerprint = useRef<string | null>(null);
  const fingerprint = status.data && jobs.data ? JSON.stringify([
    status.data.operationalStatus, status.data.approvedCopyCount, status.data.approvedVoiceCount,
    status.data.approvedImageCount, status.data.readyRenderCount, status.data.planCount,
    jobs.data.items.map(job => [job.jobId, job.status, job.completedAt]),
  ]) : null;
  useEffect(() => {
    if (fingerprint !== null && previousFingerprint.current !== null && previousFingerprint.current !== fingerprint) {
      detail.refresh(); images.refresh(); voices.refresh(); renders.refresh();
    }
    previousFingerprint.current = fingerprint;
  }, [fingerprint, detail.refresh, images.refresh, voices.refresh, renders.refresh]);
  const refresh = () => { detail.refresh(); status.refresh(); jobs.refresh(); worker.refresh(); images.refresh(); voices.refresh(); renders.refresh(); };
  return <>
    <a href="#/campaigns">← Campanhas</a><div className="section-heading"><h1>{campaignId}</h1><button onClick={refresh}>Atualizar</button></div>
    <Section title="Resumo" state={status}>
      <p>{detail.data?.character} · {status.data && statusLabel(status.data.operationalStatus)}</p>
      <p>{status.data?.nextPending ? pendingLabel(status.data.nextPending) : 'Sem pendência informada'}</p>
      {status.data && <Facts entries={[
        ['Cenas', status.data.sceneCount], ['Copies aprovadas', status.data.approvedCopyCount], ['Vozes aprovadas', status.data.approvedVoiceCount],
        ['Imagens aprovadas', status.data.approvedImageCount], ['Renders disponíveis', status.data.readyRenderCount], ['Planos existentes', status.data.planCount],
      ]} />}
    </Section>
    <Section title="Copy e cenas" state={detail}>
      {detail.data?.copyMasters.map(copy => <article key={copy.copyMasterId}><h3>{copy.copyMasterId} · v{copy.version}</h3>
        <p>{statusLabel(copy.approvalState)}</p><p>{copy.headline}</p><p>{copy.hook}</p><p>{copy.body}</p><p>{copy.cta}</p></article>)}
      <details><summary>Nova versão de copy</summary><CopyVersionForm key={campaignId} campaignId={campaignId} detail={detail} /></details>
      {detail.data?.sceneVariants.map(scene => {
        const live = status.data?.scenes.find(item => item.sceneVariantId === scene.sceneVariantId);
        return <article key={scene.sceneVariantId}><h3>{scene.variantId} · {scene.location}</h3><p>{scene.action}</p>
          <Facts entries={[
            ['Cena', scene.sceneVariantId], ['Copy', live?.currentCopyId], ['Voz', live?.currentVoiceId], ['Imagem', live?.approvedImageId],
            ['Renders', live?.readyRenderIds.join(', ') || null], ['Planos', live?.planHashes.join(', ') || null],
          ]} />{live?.pending.map(item => <p key={`${item.code}:${item.entityId}`}>{pendingLabel(item)} ({item.code})</p>)}</article>;
      })}
    </Section>
    <Section title="Imagens" state={images}>
      <ImageImportPanel key={campaignId} campaignId={campaignId} detail={detail} images={images} jobs={jobs} />
      {images.data?.items.flatMap(scene => scene.items.map(image => <article key={image.imageCandidateId}>
        <h3>{image.imageCandidateId}</h3><Facts entries={[
          ['Cena', image.sceneVariantId], ['Review', statusLabel(image.reviewStatus)], ['Origem', image.sourceKind], ['Arquivo', image.sourcePath],
          ['Dimensões', `${image.width} × ${image.height}`], ['Bytes', image.sizeBytes], ['Formato', image.format], ['SHA256', image.sha256], ['Rejeição', image.rejectionReason],
        ]} /></article>))}
      {images.data && images.data.items.every(scene => scene.items.length === 0) && <p>Nenhuma imagem importada.</p>}
    </Section>
    <Section title="Voice Masters" state={voices}>
      <VoicePanel campaignId={campaignId} detail={detail} voices={voices} jobs={jobs} />
      {voices.data?.items.map(voice => <article key={voice.voiceMasterId}><h3>{voice.voiceMasterId}</h3><Facts entries={[
        ['Copy', `${voice.copyMasterId} · v${voice.copyMasterVersion}`], ['Provider', voice.provider], ['Status', statusLabel(voice.status)], ['WAV processado', voice.processedAudioPath],
        ['Duração (s)', voice.durationSeconds], ['Transcrição', voice.transcriptMatchStatus], ['Headline falada', voice.headlineSpoken === null ? null : voice.headlineSpoken ? 'Sim' : 'Não'],
        ['QC', voice.qcFindings.join(', ') || 'Sem findings'], ['Review', voice.approvalReviewReason ?? voice.rejectionReason],
        ['Geração', voice.generation], ['SHA256 processado', voice.processedSha256], ['Aprovada em', voice.approvedAt], ['Aprovada por', voice.approvedBy],
        ['Motivo da aprovação', voice.approvalReviewReason], ['Rejeitada em', voice.rejectedAt], ['Rejeitada por', voice.rejectedBy], ['Motivo da rejeição', voice.rejectionReason],
      ]} /></article>)}
      {voices.data?.items.length === 0 && <p>Nenhum Voice Master.</p>}
    </Section>
    <Section title="HeyGen" state={renders}>
      <HeyGenPanel campaignId={campaignId} detail={detail} status={status} images={images} voices={voices} jobs={jobs} renders={renders} />
      {renders.data?.items.map(render => <article key={render.renderId}><h3>{render.renderId}</h3><Facts entries={[
        ['Status', statusLabel(render.status)], ['Cena', render.sceneVariantId], ['Imagem', render.imageCandidateId], ['Voz', render.voiceMasterId],
        ['Job', render.jobId], ['Vídeo remoto', render.remoteVideoId], ['MP4 local', render.source?.path],
        ['Vínculo manual', render.manualBinding ? 'Sim' : 'Não'], ['SHA256 imagem', render.imageSha256], ['SHA256 áudio', render.audioSha256],
        ['Criado em', render.createdAt], ['Atualizado em', render.updatedAt], ['SHA256 MP4', render.source?.sha256], ['Bytes MP4', render.source?.sizeBytes],
        ['Duração MP4 (s)', render.source?.probe.durationSec], ['Codec vídeo', render.source?.probe.video.codec],
        ['Dimensões MP4', render.source && `${render.source.probe.video.width} × ${render.source.probe.video.height}`],
        ['FPS', render.source?.probe.video.fps], ['Áudio MP4', render.source && (render.source.probe.audio
          ? `${render.source.probe.audio.codec} · ${render.source.probe.audio.sampleRate} Hz · ${render.source.probe.audio.channels} canais` : 'Sem áudio')],
        ['Avisos MP4', render.source?.probe.warnings.join(', ') || null],
      ]} />{render.source && <p>Caminho relativo ao work root. Abra o MP4 pelo Explorer; não há player ou download neste painel.</p>}
      <ErrorCode code={render.errorCode} /></article>)}
      {renders.data?.items.length === 0 && <p>Nenhum render HeyGen.</p>}
    </Section>
    <section aria-label="Edição e variantes"><h2>Edição e variantes</h2><EditingPanel campaignId={campaignId} renders={renders}/></section>
    <Section title="Jobs" state={jobs}>
      {jobs.data?.items.map(job => <article key={job.jobId}><button onClick={() => setSelectedJob(job.jobId)}>Ver Job {job.jobId}</button>
        <p>{job.jobType} · {statusLabel(job.status)} · Tentativas {job.attemptCount}/{job.maxAttempts}</p>
        <p>Próxima tentativa: {job.nextRetryAt ?? 'Não agendada'}</p><ErrorCode code={job.lastErrorCode} /></article>)}
      {jobs.data?.items.length === 0 && <p>Nenhum Job.</p>}
      {selectedJob && <JobDetail key={selectedJob} campaignId={campaignId} jobId={selectedJob} />}
    </Section>
    <Section title="Worker" state={worker}>
      <p>{worker.data?.scope === 'known' ? statusLabel(worker.data.value.state) : 'Estado desconhecido: worker não associado a esta campanha.'}</p>
      {worker.data?.scope === 'known' && <Facts entries={[
        ['Campanha', worker.data.value.campaignId], ['Tipo', worker.data.value.kind], ['Erro', worker.data.value.errorCode],
      ]} />}
      <p className="muted">Parado não significa que todos os Jobs terminaram. Nenhum worker inicia ao abrir esta página.</p>
    </Section>
    <WorkerControls campaignId={campaignId} worker={worker}
      connected={Boolean(status.data && jobs.data && worker.data && !status.error && !jobs.error && !worker.error)}
      onRefresh={() => { status.refresh(); jobs.refresh(); }} />
  </>;
}

export function JobDetail({ campaignId, jobId }: { campaignId: string; jobId: string }) {
  const job = usePolling(campaignPath(campaignId, `/jobs/${encodeURIComponent(jobId)}`), signal => read<JobSummary>(
    campaignPath(campaignId, `/jobs/${encodeURIComponent(jobId)}`), signal, value => jobSummary(value) && object(value) && value.jobId === jobId), 2000);
  return <section aria-label="Detalhe do Job"><h3>Detalhe do Job</h3><Updated state={job} />
    {job.data && <><Facts entries={[
      ['ID', job.data.jobId], ['Tipo', job.data.jobType], ['Status', statusLabel(job.data.status)], ['Retry safety', job.data.retrySafety],
      ['Tentativas', `${job.data.attemptCount}/${job.data.maxAttempts}`], ['Enfileirado', job.data.queuedAt], ['Iniciado', job.data.startedAt],
      ['Concluído', job.data.completedAt], ['Próxima tentativa', job.data.nextRetryAt],
    ]} /><ErrorCode code={job.data.lastErrorCode} />
      {job.data.jobType === 'api.local.operation' && <OperationDetail campaignId={campaignId} jobId={jobId} />}</>}
  </section>;
}

function OperationDetail({ campaignId, jobId }: { campaignId: string; jobId: string }) {
  const operation = usePolling(campaignPath(campaignId, `/operations/${encodeURIComponent(jobId)}`), signal => read<OperationView>(
    campaignPath(campaignId, `/operations/${encodeURIComponent(jobId)}`), signal, value => operationView(value) && object(value) && value.jobId === jobId), 2000);
  const result = operation.data?.result;
  const fields = result && Object.entries(result).filter(([key, value]) =>
    ['mode', 'total', 'created', 'reused', 'approved', 'manifestPath', 'imagesPath', 'variantCount', 'voiceMasterId', 'status',
      'uploadCount', 'reusedCount', 'newCount', 'reservedCount', 'maxPaidRenders', 'totalAudioSeconds'].includes(key)
    && ['string', 'number', 'boolean'].includes(typeof value));
  const nested = result && (object(result.plan) ? result.plan : object(result.render) ? result.render : null);
  return <><h4>Operação {operation.data?.operation}</h4><Updated state={operation} />
    <Facts entries={fields?.map(([label, value]) => [label, String(value)]) ?? []} />
    {nested && <Facts entries={['videoId', 'planHash', 'renderId', 'status'].filter(key => typeof nested[key] === 'string').map(key => [key, String(nested[key])])} />}
    {Array.isArray(result?.renders) && result.renders.filter(object).map(render => <p key={String(render.renderId)}>{String(render.renderId)} · {String(render.status)}</p>)}
    <ErrorCode code={operation.data?.errorCode ?? null} />
  </>;
}
