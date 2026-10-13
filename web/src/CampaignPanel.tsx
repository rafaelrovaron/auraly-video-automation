import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ApiError, campaignDetail, campaignPath, campaignStatus, campaignSummary, collection, jobSummary, object, operationView, pendingLabel, read, readWorker, sceneImages, statusLabel, voiceSummary } from './api';
import type { CampaignDetail, CampaignStatus, CampaignSummary, Items, JobSummary, OperationView, SceneImages, VoiceSummary, WorkerKind } from './api';
import { heygenRenderView } from './heygenApi';
import type { HeyGenRenderView } from './heygenApi';
import { usePolling } from './usePolling';
import type { RemoteState } from './usePolling';
import { WorkerControls } from './WorkerControls';
import { CampaignCreateForm, CampaignNameForm, CopyVersionForm } from './CampaignForms';
import { ImageImportPanel } from './ImageImportPanel';
import { VoicePanel } from './VoicePanel';
import { HeyGenPanel } from './HeyGenPanel';
import { EditingPanel } from './EditingPanel';
import { FinalizationPanel } from './FinalizationPanel';
import type { EditBatchPlan } from './editingApi';
import { campaignStagePath, campaignStages } from './campaignStages';
import type { CampaignStage } from './campaignStages';

function Updated({ state }: { state: RemoteState<unknown> }) {
  return <>
    {state.error && <p role="alert">{state.error.message} ({state.error.code}) {state.data ? 'Dados desatualizados.' : 'Seção indisponível.'}</p>}
    {state.loading && !state.data && <p role="status">Carregando…</p>}
    {state.lastSuccessAt !== null && <small>Última leitura: {new Date(state.lastSuccessAt).toLocaleString('pt-BR')}</small>}
  </>;
}

function Section({ title, state, children, hidden }: { title: string; state: RemoteState<unknown>; children: ReactNode; hidden?: boolean }) {
  return <section aria-label={title} hidden={hidden}><h2>{title}</h2><Updated state={state} />{state.data !== null && children}</section>;
}

function Facts({ entries }: { entries: [string, ReactNode][] }) {
  return <dl>{entries.map(([label, value]) => <div className="fact" key={label}><dt>{label}</dt><dd>{value ?? 'Não disponível'}</dd></div>)}</dl>;
}

function TechnicalDetails({ entries }: { entries: [string, ReactNode][] }) {
  return <details><summary>Detalhes técnicos</summary><Facts entries={entries} /></details>;
}

function fileName(path: string): string { return path.split(/[\\/]/).at(-1) || path; }

function taskLabel(type: string): string {
  return ({'api.local.operation': 'Operação local', 'voice.generate': 'Geração de voz', 'voice.import': 'Importação de voz',
    'heygen.asset.upload': 'Envio de assets HeyGen', 'heygen.video.generate': 'Geração de vídeo',
    'editing.render': 'Vídeo final'} as Record<string, string>)[type] ?? 'Tarefa';
}

function ErrorCode({ code }: { code: string | null }) {
  return code ? <><p role="status">{/oauth|auth|credential|account/i.test(code)
    ? 'Conecte o HeyGen pelo fluxo OAuth existente da CLI.' : 'Esta tarefa precisa de atenção. Consulte os detalhes técnicos.'}</p>
    <details><summary>Detalhes técnicos do erro</summary><p>{code}</p></details></> : null;
}

export function CampaignList() {
  const state = usePolling('campaign-list', signal => read<Items<CampaignSummary>>('/api/v1/campaigns', signal, value => collection(value, campaignSummary)), 5000);
  return <>
    <div className="section-heading"><h1>Campanhas</h1><button onClick={state.refresh}>Atualizar</button></div>
    <p>Consulte o andamento da produção. Nenhuma ação paga é iniciada ao abrir o painel.</p><Updated state={state} />
    {state.data?.items.length === 0 && <p>Nenhuma campanha. Use Criar campanha abaixo.</p>}
    <details><summary>Criar campanha</summary><CampaignCreateForm onCreated={campaignId => { window.location.hash = `#/campaigns/${encodeURIComponent(campaignId)}`; }} /></details>
    <div className="campaign-list">{state.data?.items.map(item => {
      const duplicateName = item.displayName && state.data?.items.some(other =>
        other.campaignId !== item.campaignId && other.displayName?.trim().toLocaleLowerCase() === item.displayName?.trim().toLocaleLowerCase());
      return <article key={item.campaignId}>
      <h2><a href={`#/campaigns/${encodeURIComponent(item.campaignId)}`}>{item.displayName || item.campaignId}{duplicateName && ` · ${item.campaignId.slice(-8)}`}</a></h2>
      <p>{item.character}</p><p>{item.sceneCount} cenas · {statusLabel(item.operationalStatus)}</p>
      <p>{item.nextPending ? pendingLabel(item.nextPending) : 'Sem pendência informada'}</p>
      {item.displayName && <details><summary>Detalhes técnicos</summary><p>{item.campaignId}</p></details>}
    </article>;})}</div>
  </>;
}

export function CampaignDetailPanel({ campaignId, stage }: { campaignId: string; stage?: CampaignStage }) {
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
  const [finalizationPlan, setFinalizationPlan] = useState<EditBatchPlan | null>(null);
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
  const workerConnected = Boolean(status.data && jobs.data && worker.data && !status.error && !jobs.error && !worker.error);
  const stageWorkerKind: Record<CampaignStage, WorkerKind> = {overview: 'local_operations', script: 'local_operations', images: 'local_operations',
    voice: 'voice_generate', videos: 'local_operations', editing: 'local_operations', finalization: 'local_operations'};
  return <>
    <a href="#/campaigns">← Campanhas</a><div className="section-heading"><h1>{detail.data?.displayName || campaignId}</h1><button onClick={refresh}>Atualizar</button></div>
    {stage && <>
      <nav className="campaign-stage-nav" aria-label="Etapas da campanha">
        {campaignStages.map(([slug, label]) => <a key={slug} href={campaignStagePath(campaignId, slug)} aria-current={stage === slug ? 'page' : undefined}>{label}</a>)}
      </nav>
      <label className="campaign-stage-picker">Etapa da campanha
        <select value={stage} onChange={event => { window.location.hash = campaignStagePath(campaignId, event.target.value as CampaignStage); }}>
          {campaignStages.map(([slug, label]) => <option key={slug} value={slug}>{label}</option>)}
        </select>
      </label>
    </>}
    <Section title="Resumo" state={status} hidden={!!stage && stage !== 'overview'}>
      <p>{detail.data?.character} · {status.data && statusLabel(status.data.operationalStatus)}</p>
      <p>{status.data?.nextPending ? pendingLabel(status.data.nextPending) : 'Sem pendência informada'}</p>
      {detail.data && <details><summary>{detail.data.displayName ? 'Alterar nome da campanha' : 'Adicionar nome à campanha'}</summary>
        <CampaignNameForm key={campaignId} campaignId={campaignId} currentName={detail.data.displayName ?? null} onSaved={detail.refresh} />
      </details>}
      {status.data && <Facts entries={[
        ['Cenas', status.data.sceneCount], ['Copies aprovadas', status.data.approvedCopyCount], ['Vozes aprovadas', status.data.approvedVoiceCount],
        ['Imagens aprovadas', status.data.approvedImageCount], ['Renders disponíveis', status.data.readyRenderCount], ['Planos existentes', status.data.planCount],
      ]} />}
    </Section>
    <Section title="Copy e cenas" state={detail} hidden={!!stage && stage !== 'script'}>
      {detail.data?.copyMasters.map(copy => <article key={copy.copyMasterId}><h3>Roteiro v{copy.version}</h3>
        <p>{statusLabel(copy.approvalState)}</p><p>{copy.headline}</p><p>{copy.hook}</p><p>{copy.body}</p><p>{copy.cta}</p>
        <TechnicalDetails entries={[["ID do roteiro", copy.copyMasterId], ["SHA-256", copy.sha256]]} /></article>)}
      <details><summary>Nova versão de copy</summary><CopyVersionForm key={campaignId} campaignId={campaignId} detail={detail} /></details>
      {detail.data?.sceneVariants.map(scene => {
        const live = status.data?.scenes.find(item => item.sceneVariantId === scene.sceneVariantId);
        return <article key={scene.sceneVariantId}><h3>Cena · {scene.location}</h3><p>{scene.action}</p>
          <Facts entries={[
            ['Roteiro', live?.currentCopyId ? 'Disponível' : 'Pendente'], ['Voz', live?.currentVoiceId ? 'Disponível' : 'Pendente'],
            ['Imagem', live?.approvedImageId ? 'Aprovada' : 'Pendente'], ['Vídeos', live?.readyRenderIds.length ?? 0], ['Planos', live?.planHashes.length ?? 0],
          ]} /><TechnicalDetails entries={[
            ['ID da cena', scene.sceneVariantId], ['Código da variante', scene.variantId], ['ID do roteiro', live?.currentCopyId],
            ['ID da voz', live?.currentVoiceId], ['ID da imagem', live?.approvedImageId],
            ['IDs dos vídeos', live?.readyRenderIds.join(', ') || null], ['Hashes dos planos', live?.planHashes.join(', ') || null],
          ]} />{live?.pending.map(item => <p key={`${item.code}:${item.entityId}`}>{pendingLabel(item)}</p>)}</article>;
      })}
    </Section>
    <Section title="Imagens" state={images} hidden={!!stage && stage !== 'images'}>
      <ImageImportPanel key={campaignId} campaignId={campaignId} detail={detail} images={images} jobs={jobs} />
      {images.data?.items.flatMap(scene => scene.items.map(image => <article key={image.imageCandidateId}>
        <h3>Imagem · {detail.data?.sceneVariants.find(item => item.sceneVariantId === image.sceneVariantId)?.location || 'Cena'} · {fileName(image.sourcePath)}</h3>
        {image.sha256 && <img className="image-preview" loading="lazy" alt={`Imagem · ${detail.data?.sceneVariants.find(item => item.sceneVariantId === image.sceneVariantId)?.location || 'Cena'}`} src={campaignPath(campaignId, `/images/${encodeURIComponent(image.imageCandidateId)}/media/${image.sha256}`)} />}
        <Facts entries={[
          ['Estado', statusLabel(image.reviewStatus)], ['Origem', image.sourceKind],
          ['Dimensões', `${image.width} × ${image.height}`], ['Tamanho', `${(image.sizeBytes / 1024).toFixed(1)} KB`],
          ['Rejeição', image.rejectionReason],
        ]} /><TechnicalDetails entries={[
          ['ID da imagem', image.imageCandidateId], ['ID da cena', image.sceneVariantId], ['Caminho', image.sourcePath],
          ['Bytes', image.sizeBytes], ['Formato', image.format], ['SHA-256', image.sha256],
        ]} /></article>))}
      {images.data && images.data.items.every(scene => scene.items.length === 0) && <p>Nenhuma imagem importada.</p>}
    </Section>
    <Section title="Voice Masters" state={voices} hidden={!!stage && stage !== 'voice'}>
      <VoicePanel campaignId={campaignId} detail={detail} voices={voices} jobs={jobs} />
      {voices.data?.items.map(voice => <article key={voice.voiceMasterId}><h3>Áudio principal · roteiro v{voice.copyMasterVersion} · geração {voice.generation}</h3><Facts entries={[
        ['Origem', voice.provider], ['Estado', statusLabel(voice.status)],
        ['Duração (s)', voice.durationSeconds], ['Transcrição', voice.transcriptMatchStatus], ['Headline falada', voice.headlineSpoken === null ? null : voice.headlineSpoken ? 'Sim' : 'Não'],
        ['QC', voice.qcFindings.join(', ') || 'Sem findings'], ['Review', voice.approvalReviewReason ?? voice.rejectionReason],
        ['Aprovada em', voice.approvedAt], ['Aprovada por', voice.approvedBy],
        ['Motivo da aprovação', voice.approvalReviewReason], ['Rejeitada em', voice.rejectedAt], ['Rejeitada por', voice.rejectedBy], ['Motivo da rejeição', voice.rejectionReason],
      ]} /><TechnicalDetails entries={[
        ['ID da voz', voice.voiceMasterId], ['ID do roteiro', voice.copyMasterId], ['WAV processado', voice.processedAudioPath],
        ['SHA-256 processado', voice.processedSha256],
      ]} /></article>)}
      {voices.data?.items.length === 0 && <p>Nenhum Voice Master.</p>}
    </Section>
    <Section title="HeyGen" state={renders} hidden={!!stage && stage !== 'videos'}>
      <HeyGenPanel campaignId={campaignId} detail={detail} status={status} images={images} voices={voices} jobs={jobs} renders={renders} />
      {renders.data?.items.map(render => <article key={render.renderId}>
        <h3>Vídeo · {detail.data?.sceneVariants.find(item => item.sceneVariantId === render.sceneVariantId)?.location || 'Cena'}</h3><Facts entries={[
        ['Estado', statusLabel(render.status)], ['Vínculo manual', render.manualBinding ? 'Sim' : 'Não'],
        ['Criado em', new Date(render.createdAt).toLocaleString('pt-BR')],
        ['Atualizado em', new Date(render.updatedAt).toLocaleString('pt-BR')],
        ['Tamanho MP4', render.source && `${(render.source.sizeBytes / 1024 / 1024).toFixed(1)} MB`],
        ['Duração MP4 (s)', render.source?.probe.durationSec], ['Codec vídeo', render.source?.probe.video.codec],
        ['Dimensões MP4', render.source && `${render.source.probe.video.width} × ${render.source.probe.video.height}`],
        ['FPS', render.source?.probe.video.fps], ['Áudio MP4', render.source && (render.source.probe.audio
          ? `${render.source.probe.audio.codec} · ${render.source.probe.audio.sampleRate} Hz · ${render.source.probe.audio.channels} canais` : 'Sem áudio')],
        ['Avisos MP4', render.source?.probe.warnings.join(', ') || null],
      ]} /><TechnicalDetails entries={[
        ['ID do vídeo', render.renderId], ['ID da cena', render.sceneVariantId], ['ID da imagem', render.imageCandidateId],
        ['ID da voz', render.voiceMasterId], ['ID da tarefa', render.jobId], ['Vídeo remoto', render.remoteVideoId],
        ['MP4 local', render.source?.path], ['SHA-256 MP4', render.source?.sha256], ['Bytes MP4', render.source?.sizeBytes],
        ['SHA-256 imagem', render.imageSha256], ['SHA-256 áudio', render.audioSha256],
      ]} />{render.source && <p>Caminho relativo ao work root. Abra o MP4 pelo Explorer; não há player ou download neste painel.</p>}
      <ErrorCode code={render.errorCode} /></article>)}
      {renders.data?.items.length === 0 && <p>Nenhum render HeyGen.</p>}
    </Section>
    <section aria-label="Edição e variantes" hidden={!!stage && stage !== 'editing'}><h2>Edição e variantes</h2>
      <EditingPanel campaignId={campaignId} renders={renders} scenes={detail.data?.sceneVariants}
        onRenderPlanChange={stage ? setFinalizationPlan : undefined}/>
    </section>
    {stage && <section aria-label="Finalização" hidden={stage !== 'finalization'}><h2>Finalização</h2>
      <FinalizationPanel campaignId={campaignId} active={stage === 'finalization'} preferredPlan={finalizationPlan}/>
    </section>}
    {stage && stage !== 'overview' && <section aria-label="Processamento desta etapa"><h2>Processamento desta etapa</h2>
      <p>Escolha o tipo de tarefa desta etapa e consulte o andamento. Iniciar pode processar outras tarefas já na fila desta campanha; nada começa só por abrir a etapa.</p>
      {stage === 'videos' && <p>Primeiro processe as operações locais de preparação; depois selecione upload de assets HeyGen e vídeos HeyGen conforme as tarefas aparecerem.</p>}
      {stage === 'editing' && <p>Primeiro processe as operações locais de validação e salvamento; depois selecione renderizar planos salvos na finalização.</p>}
      <WorkerControls key={stage} campaignId={campaignId} initialKind={stageWorkerKind[stage]} worker={worker} connected={workerConnected}
        onRefresh={() => { status.refresh(); jobs.refresh(); }} />
    </section>}
    <div hidden={!!stage && stage !== 'overview'}>
    <Section title="Jobs" state={jobs}>
      {jobs.data?.items.map(job => <article key={job.jobId}><button onClick={() => setSelectedJob(job.jobId)}>Ver tarefa · {taskLabel(job.jobType)} · {statusLabel(job.status)} · {job.jobId.slice(-4)}</button>
        <p>Tentativas {job.attemptCount}/{job.maxAttempts}</p>
        <p>Próxima tentativa: {job.nextRetryAt ? new Date(job.nextRetryAt).toLocaleString('pt-BR') : 'Não agendada'}</p>
        <ErrorCode code={job.lastErrorCode} /><TechnicalDetails entries={[["ID", job.jobId], ["Tipo", job.jobType]]} /></article>)}
      {jobs.data?.items.length === 0 && <p>Nenhuma tarefa.</p>}
      {selectedJob && <JobDetail key={selectedJob} campaignId={campaignId} jobId={selectedJob} />}
    </Section>
    <Section title="Processamento" state={worker}>
      <p>{worker.data?.scope === 'known' ? statusLabel(worker.data.value.state) : 'Estado desconhecido: worker não associado a esta campanha.'}</p>
      {worker.data?.scope === 'known' && <TechnicalDetails entries={[
        ['Campanha', worker.data.value.campaignId], ['Tipo', worker.data.value.kind], ['Erro', worker.data.value.errorCode],
      ]} />}
      <p className="muted">Parado não significa que todos os Jobs terminaram. Nenhum worker inicia ao abrir esta página.</p>
    </Section>
    <WorkerControls campaignId={campaignId} worker={worker}
      connected={workerConnected}
      onRefresh={() => { status.refresh(); jobs.refresh(); }} />
    </div>
  </>;
}

export function JobDetail({ campaignId, jobId }: { campaignId: string; jobId: string }) {
  const job = usePolling(campaignPath(campaignId, `/jobs/${encodeURIComponent(jobId)}`), signal => read<JobSummary>(
    campaignPath(campaignId, `/jobs/${encodeURIComponent(jobId)}`), signal, value => jobSummary(value) && object(value) && value.jobId === jobId), 2000);
  return <section aria-label="Detalhe do Job"><h3>Detalhe do Job</h3><Updated state={job} />
    {job.data && <><Facts entries={[
      ['Tarefa', taskLabel(job.data.jobType)], ['Estado', statusLabel(job.data.status)],
      ['Tentativas', `${job.data.attemptCount}/${job.data.maxAttempts}`], ['Enfileirado', job.data.queuedAt], ['Iniciado', job.data.startedAt],
      ['Concluído', job.data.completedAt], ['Próxima tentativa', job.data.nextRetryAt],
    ]} /><TechnicalDetails entries={[["ID", job.data.jobId], ["Tipo", job.data.jobType], ["Segurança de repetição", job.data.retrySafety]]} />
      <ErrorCode code={job.data.lastErrorCode} />
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
