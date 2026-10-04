import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, campaignPath, jobSummary, object, post, read } from './api';
import type { CampaignDetail, Items, JobSummary, VoiceSummary } from './api';
import { campaignBudgetView, voiceSubmission, voiceOperationView, permitsTranscriptReview } from './voiceApi';
import type { CampaignBudgetView, VoiceOperationView } from './voiceApi';
import { usePolling } from './usePolling';
import type { RemoteState } from './usePolling';
import { useUnsavedChanges } from './useUnsavedChanges';

type Props = {campaignId: string; detail: RemoteState<CampaignDetail>; voices: RemoteState<Items<VoiceSummary>>; jobs: RemoteState<Items<JobSummary>>};
type FormSection = 'budget' | 'generation' | 'import' | 'review';
const positive = (value: string) => /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) > 0;
const actorValid = (value: string) => /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,119}$/.test(value);
const sourceValid = (value: string) => !!value && !/^[\\/]|:/.test(value) && !value.split(/[\\/]/).some(part => ['..', '.', ''].includes(part)) && /\.(mp3|wav)$/i.test(value);

export function VoicePanel(props: Props) { return <VoiceForms key={props.campaignId} {...props} />; }

function VoiceForms({campaignId, detail, voices, jobs}: Props) {
  const [version, setVersion] = useState('');
  const [currency, setCurrency] = useState('');
  const [limit, setLimit] = useState('');
  const [budgetConfirmed, setBudgetConfirmed] = useState(false);
  const [voiceId, setVoiceId] = useState('');
  const [modelId, setModelId] = useState('');
  const [actor, setActor] = useState('');
  const [ceiling, setCeiling] = useState('');
  const [paid, setPaid] = useState(false);
  const [busy, setBusy] = useState(false);
  const [unknown, setUnknown] = useState(false);
  const [notice, setNotice] = useState('');
  const [selectedJob, setSelectedJob] = useState('');
  const [acceptedVoice, setAcceptedVoice] = useState<string | null>(null);
  const [budgetIntent, setBudgetIntent] = useState<{currency: string; limitCents: number; minimumRead: number} | null>(null);
  const [dirty, setDirty] = useState({budget: false, generation: false, import: false, review: false});
  const [sourcePath, setSourcePath] = useState('');
  const [importConfirmed, setImportConfirmed] = useState(false);
  const [requestId, setRequestId] = useState('');
  const [wrapper, setWrapper] = useState<{jobId: string; kind: 'voice_import' | 'voice_review' | null; voiceId?: string} | null>(null);
  const [selectedVoice, setSelectedVoice] = useState('');
  const [reviewActor, setReviewActor] = useState('');
  const [reason, setReason] = useState('');
  const [listened, setListened] = useState(false);
  const [rejectionConfirmed, setRejectionConfirmed] = useState(false);
  const [reviewObservation, setReviewObservation] = useState<{voiceId: string; status: string; minimumRead: number} | null>(null);
  const [reviewNotice, setReviewNotice] = useState('');
  useUnsavedChanges(Object.values(dirty).some(Boolean) || unknown || busy);
  const markDirty = (form: FormSection) => setDirty(previous => ({...previous, [form]: true}));
  const clearForm = useCallback((form: FormSection) => setDirty(previous => ({...previous, [form]: false})), []);
  const alive = useRef(true), lock = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const budget = usePolling(`${campaignId}:budget`, signal => read<CampaignBudgetView>(campaignPath(campaignId, '/budget'), signal, campaignBudgetView), null);
  const connected = Boolean(detail.data && voices.data && jobs.data && !detail.error && !voices.error && !jobs.error
    && detail.data.campaignId === campaignId && voices.data.items.every(voice => voice.campaignId === campaignId)
    && jobs.data.items.every(job => job.campaignId === campaignId));
  const budgetFresh = budget.data && !budget.error;
  const blocked = !connected || busy || unknown || budgetIntent !== null;
  const copy = detail.data?.copyMasters.find(item => item.version === Number(version) && item.approvalState === 'approved');
  const edit = (action: () => void, form: FormSection | 'copy') => {
    action(); setBudgetConfirmed(false); setPaid(false); setImportConfirmed(false);
    if (form === 'copy') setDirty(previous => ({...previous,
      generation: previous.generation || Boolean(voiceId || modelId || actor || ceiling), import: previous.import || Boolean(sourcePath)}));
    else markDirty(form);
  };
  const canGenerate = !blocked && budgetFresh && budget.data?.state === 'configured' && copy
    && /^[A-Za-z0-9_-]{1,120}$/.test(voiceId) && /^[A-Za-z0-9_-]{1,120}$/.test(modelId)
    && actorValid(actor) && positive(ceiling) && Number(ceiling) <= budget.data.limitCents && paid;
  const reviewVoice = voices.data?.items.find(item => item.voiceMasterId === selectedVoice && item.campaignId === campaignId);
  const exception = reviewVoice ? permitsTranscriptReview(reviewVoice) : false;
  const reviewable = !blocked && reviewVoice?.status === 'review_required' && actorValid(reviewActor);
  const canApprove = reviewable && reviewVoice?.processedAudioPath && reviewVoice.processedSha256 && reviewVoice.headlineSpoken === false
    && (exception ? reason.trim().length > 0 : reviewVoice.transcriptMatchStatus === 'matched' && reviewVoice.qcFindings.length === 0) && listened;
  const canReject = reviewable && reason.trim().length > 0 && rejectionConfirmed;
  const editReview = (action: () => void) => {action(); setListened(false); setRejectionConfirmed(false); markDirty('review');};
  const completedReview = useCallback(() => {
    const minimumRead = voices.refresh();
    setReviewObservation(previous => previous ? {...previous, minimumRead} : null);
  }, [voices.refresh]);
  useEffect(() => {
    if (!reviewObservation || voices.error || (voices.lastSuccessReadId ?? 0) < reviewObservation.minimumRead) return;
    const current = voices.data?.items.find(item => item.voiceMasterId === reviewObservation.voiceId && item.campaignId === campaignId);
    if (current && current.status !== reviewObservation.status) {
      setReviewNotice(`Estado observado: ${current.voiceMasterId} · ${current.status}; não confirma autoria do comando.`);
      setReviewObservation(null);
    }
  }, [reviewObservation, voices.data, voices.error, voices.lastSuccessReadId, campaignId]);

  useEffect(() => {
    if (!budgetIntent || budget.error || (budget.lastSuccessReadId ?? 0) < budgetIntent.minimumRead) return;
    if (budget.data?.state === 'configured') {
      setNotice(budget.data.currency === budgetIntent.currency && budget.data.limitCents === budgetIntent.limitCents
        ? 'Configuração solicitada observada. Isso não autoriza geração nem informa autoria.' : 'Outro orçamento foi observado. Não será sobrescrito.');
      setBudgetIntent(null); clearForm('budget');
    }
  }, [budget.data, budget.error, budget.lastSuccessReadId, budgetIntent, clearForm]);

  const saveBudget = async () => {
    if (lock.current || blocked || !budgetFresh || budget.data?.state !== 'missing' || !budgetConfirmed
      || !/^[A-Z]{3}$/.test(currency) || !positive(limit)) return;
    lock.current = true; setBusy(true); setNotice(''); setBudgetConfirmed(false);
    const intent = {currency, limitCents: Number(limit), minimumRead: 0};
    let uncertain = false;
    try {
      const result = await post<unknown>(campaignPath(campaignId, '/budget'), {...intent, minimumRead: undefined, confirmed: true});
      if (!campaignBudgetView(result) || result.state !== 'configured' || result.currency !== intent.currency || result.limitCents !== intent.limitCents)
        throw new ApiError('command_unknown');
      uncertain = true;
    } catch (error) {
      if (!alive.current) return;
      const failure = error instanceof ApiError ? error : new ApiError('command_unknown');
      uncertain = failure.code === 'command_unknown'; setNotice(`${failure.code}: ${failure.message}`);
    } finally {
      if (alive.current) {
        intent.minimumRead = budget.refresh(); if (uncertain) setBudgetIntent(intent);
        setBusy(false); lock.current = false;
      }
    }
  };

  const generate = async () => {
    if (lock.current || !canGenerate) return;
    lock.current = true; setBusy(true); setPaid(false); setNotice('');
    try {
      const result = await post<unknown>(campaignPath(campaignId, '/voices/generate'), {campaignId, copyMasterVersion: Number(version),
        voiceId, modelId, voiceSettings: {}, outputFormat: 'mp3_44100_128', transcriptMatchThreshold: 0.97,
        paidRequestApproved: true, paidRequestApprovedBy: actor, approvedBudgetCents: Number(ceiling)});
      if (!alive.current) return;
      if (!voiceSubmission(result) || result.operation !== 'voice_generate' || result.campaignId !== campaignId) throw new ApiError('command_unknown');
      setSelectedJob(result.jobId); setAcceptedVoice(result.voiceMasterId!); clearForm('generation');
      setNotice('Job na fila. Inicie voice_generate explicitamente; isso não aprova o áudio.');
    } catch (error) {
      if (!alive.current) return;
      const failure = error instanceof ApiError ? error : new ApiError('command_unknown');
      setUnknown(failure.code === 'command_unknown'); setNotice(`${failure.code}: ${failure.message}`);
    } finally {
      if (alive.current) { jobs.refresh(); voices.refresh(); setBusy(false); lock.current = false; }
    }
  };

  const importVoice = async () => {
    if (lock.current || blocked || !copy || !sourceValid(sourcePath) || !importConfirmed) return;
    lock.current = true; setBusy(true); setImportConfirmed(false); setNotice('');
    const id = crypto.randomUUID(); setRequestId(id);
    try {
      const result = await post<unknown>(campaignPath(campaignId, '/voices/import'), {operation: 'voice_import', campaignId, sourcePath,
        requestId: id, request: {campaignId, copyMasterVersion: Number(version)}});
      if (!alive.current) return;
      if (!voiceSubmission(result) || result.operation !== 'voice_import' || result.campaignId !== campaignId) throw new ApiError('command_unknown');
      setWrapper({jobId: result.jobId, kind: 'voice_import'}); clearForm('import');
      setNotice('Importação enfileirada. Inicie local_operations; depois, voice_import. Nenhum worker foi iniciado.');
    } catch (error) {
      if (!alive.current) return;
      const failure = error instanceof ApiError ? error : new ApiError('command_unknown');
      setUnknown(failure.code === 'command_unknown'); setNotice(`${failure.code}: ${failure.message}`);
    } finally {
      if (alive.current) { jobs.refresh(); voices.refresh(); setBusy(false); lock.current = false; }
    }
  };

  const review = async (action: 'approve' | 'reject') => {
    if (lock.current || !reviewVoice || !(action === 'approve' ? canApprove : canReject)) return;
    const intent = {voiceId: reviewVoice.voiceMasterId, status: reviewVoice.status, minimumRead: 0};
    lock.current = true; setBusy(true); setListened(false); setRejectionConfirmed(false); setNotice(''); setReviewNotice('');
    let observe = false;
    try {
      const result = await post<unknown>(campaignPath(campaignId, `/voices/${encodeURIComponent(intent.voiceId)}/review`), {
        operation: 'voice_review', campaignId, voiceId: intent.voiceId, action, actor: reviewActor, reason: action === 'reject' || exception ? reason.trim() : null});
      if (!alive.current) return;
      if (!voiceSubmission(result) || result.operation !== 'voice_review' || result.campaignId !== campaignId) throw new ApiError('command_unknown');
      observe = true; setWrapper({jobId: result.jobId, kind: 'voice_review', voiceId: intent.voiceId});
      setNotice('Review enfileirado, não é aprovação. Inicie local_operations e consulte a voz persistida.');
      clearForm('review');
    } catch (error) {
      if (!alive.current) return;
      const failure = error instanceof ApiError ? error : new ApiError('command_unknown');
      observe = failure.code === 'command_unknown'; setUnknown(observe); setNotice(`${failure.code}: ${failure.message}`);
    } finally {
      if (alive.current) {
        intent.minimumRead = voices.refresh(); if (observe) setReviewObservation(intent);
        jobs.refresh(); setBusy(false); lock.current = false;
      }
    }
  };

  return <section aria-label="Operação de voz"><h3>Operar Voice Master</h3>
    {!connected && <p role="alert">Leituras de voz desatualizadas ou indisponíveis. Atualize a campanha.</p>}
    <section aria-label="Orçamento da campanha"><h4>Orçamento da campanha</h4>
      {budget.error && <p role="alert">{budget.error.message} ({budget.error.code}). Dados desatualizados.</p>}
      {budget.data?.state === 'configured' && <p>{budget.data.currency} · limite {budget.data.limitCents} centavos</p>}
      {budget.data?.state === 'invalid' && <p role="alert">Orçamento legado inválido. Geração bloqueada; este formulário não repara configurações existentes.</p>}
      {budget.data?.state === 'missing' && <form aria-label="Orçamento inicial" onSubmit={event => {event.preventDefault(); void saveBudget();}}>
        <fieldset disabled={blocked || !budgetFresh}>
          <label>Moeda da campanha <input value={currency} maxLength={3} onChange={event => edit(() => setCurrency(event.target.value), 'budget')} /></label>
          <label>Limite da campanha (centavos) <input type="number" min="1" step="1" value={limit} onChange={event => edit(() => setLimit(event.target.value), 'budget')} /></label>
          <label><input type="checkbox" checked={budgetConfirmed} onChange={event => {setBudgetConfirmed(event.target.checked); markDirty('budget');}} />Confirmo este orçamento inicial</label>
          <button disabled={!budgetConfirmed || !/^[A-Z]{3}$/.test(currency) || !positive(limit)}>Configurar orçamento inicial</button>
        </fieldset>
      </form>}
      <p>Configuração inicial somente: moeda/limite definidos não são editáveis aqui. Não é saldo disponível nem estimativa de preço e não autoriza gasto.</p>
      <button type="button" onClick={budget.refresh}>Consultar orçamento</button>
    </section>
    <label>Versão da copy para voz <select value={version} disabled={busy || unknown} onChange={event => edit(() => setVersion(event.target.value), 'copy')}>
      <option value="">Selecione a versão aprovada</option>{detail.data?.copyMasters.filter(item => item.approvalState === 'approved').map(item => <option key={item.copyMasterId} value={item.version}>v{item.version}</option>)}
    </select></label>
    {copy && <div><p>Headline visual (não narrada): {copy.headline}</p><p>Texto narrado: {copy.hook}{'\n\n'}{copy.body}{'\n\n'}{copy.cta}</p></div>}
    <form aria-label="Geração de voz" onSubmit={event => {event.preventDefault(); void generate();}}><fieldset disabled={blocked}>
      <label>Voice ID <input value={voiceId} onChange={event => edit(() => setVoiceId(event.target.value), 'generation')} /></label>
      <label>Model ID <input value={modelId} onChange={event => edit(() => setModelId(event.target.value), 'generation')} /></label>
      <label>Responsável pela geração <input value={actor} onChange={event => edit(() => setActor(event.target.value), 'generation')} /></label>
      <label>Teto desta geração (centavos) <input type="number" min="1" step="1" value={ceiling} onChange={event => edit(() => setCeiling(event.target.value), 'generation')} /></label>
      <label><input type="checkbox" checked={paid} onChange={event => {setPaid(event.target.checked); markDirty('generation');}} />Autorizo o gasto desta geração</label>
      <button disabled={!canGenerate}>Enfileirar geração de voz</button>
    </fieldset></form>
    <p>IDs do ElevenLabs são informados manualmente. A mesma identidade pode reutilizar voz/Job; não há regeneração forçada.</p>
    <form aria-label="Importação de voz" onSubmit={event => {event.preventDefault(); void importVoice();}}><fieldset disabled={blocked}>
      <label>Caminho relativo do áudio <input value={sourcePath} onChange={event => edit(() => setSourcePath(event.target.value), 'import')} /></label>
      <label><input type="checkbox" checked={importConfirmed} onChange={event => {setImportConfirmed(event.target.checked); markDirty('import');}} />Confirmo o arquivo e a versão da copy</label>
      <button disabled={blocked || !copy || !sourceValid(sourcePath) || !importConfirmed}>Enfileirar importação de voz</button>
    </fieldset></form>
    <p>Copie o MP3/WAV pelo Explorer para dentro do project root, por exemplo imports/voice.wav (máximo 100 MiB). Informe o caminho relativo; não há upload. O original é preservado e o WAV processado é outro arquivo. Importar não exige orçamento ElevenLabs.</p>
    {requestId && <p>Request de importação: {requestId}</p>}
    {notice && <p role="status">{notice}</p>}
    {unknown && <p>Intenção anterior preservada, sem reenvio. Inspecione Jobs e vozes persistidos; coincidência de versão/provider não prova identidade.</p>}
    <label>Job de geração para inspecionar <select value={selectedJob} disabled={!connected || busy} onChange={event => {setSelectedJob(event.target.value); setAcceptedVoice(null);}}>
      <option value="">Selecione um Job</option>{selectedJob && !jobs.data?.items.some(item => item.jobId === selectedJob) && <option value={selectedJob}>{selectedJob}</option>}
      {jobs.data?.items.filter(item => item.campaignId === campaignId && item.jobType === 'voice.generate').map(item => <option key={item.jobId} value={item.jobId}>{item.jobId}</option>)}
    </select></label>
    {acceptedVoice && <p>Voice Master aceito: {acceptedVoice}</p>}
    {selectedJob && <VoiceJobMonitor key={selectedJob} campaignId={campaignId} jobId={selectedJob} kind="voice.generate" onChange={voices.refresh} />}
    <label>Operação local de voz para inspecionar <select value={wrapper?.jobId ?? ''} disabled={!connected || busy}
      onChange={event => setWrapper(event.target.value ? {jobId: event.target.value, kind: null} : null)}>
      <option value="">Selecione uma operação</option>{wrapper && !jobs.data?.items.some(item => item.jobId === wrapper.jobId) && <option value={wrapper.jobId}>{wrapper.jobId}</option>}
      {jobs.data?.items.filter(item => item.campaignId === campaignId && item.jobType === 'api.local.operation').map(item => <option key={item.jobId} value={item.jobId}>{item.jobId}</option>)}
    </select></label>
    <p>Inspecionar um Job conhecido não o associa a uma intenção cuja resposta foi perdida.</p>
    {wrapper && <VoiceOperationMonitor key={wrapper.jobId} campaignId={campaignId} jobId={wrapper.jobId} kind={wrapper.kind} voiceId={wrapper.voiceId}
      onChange={voices.refresh} onComplete={completedReview} />}
    <label>Voice Master para revisar <select value={selectedVoice} disabled={!connected || busy} onChange={event => {
      setSelectedVoice(event.target.value); setReviewActor(''); setReason(''); setListened(false); setRejectionConfirmed(false); setReviewNotice(''); markDirty('review');
    }}><option value="">Selecione uma voz persistida</option>{voices.data?.items.filter(item => item.campaignId === campaignId).map(item =>
      <option key={item.voiceMasterId} value={item.voiceMasterId}>{item.voiceMasterId} · v{item.copyMasterVersion} · {item.status}</option>)}
    </select></label>
    {reviewVoice && <div><p>Revisando {reviewVoice.voiceMasterId} · copy v{reviewVoice.copyMasterVersion} · {reviewVoice.provider} · {reviewVoice.status}</p>
      <p>WAV: {reviewVoice.processedAudioPath ?? 'Não disponível'} · SHA256: {reviewVoice.processedSha256 ?? 'Não disponível'} · duração: {reviewVoice.durationSeconds ?? 'Não disponível'} s</p>
      <p>Transcrição: {reviewVoice.transcriptMatchStatus ?? 'Não disponível'} · headline falada: {String(reviewVoice.headlineSpoken)} · QC: {reviewVoice.qcFindings.join('; ') || 'Sem findings'}</p>
      <p>Ouça o WAV fora do painel: combine o caminho relativo acima com o work root configurado no servidor. A existência do arquivo não significa aprovação.</p>
      {exception && <p>Exceção restrita: transcrição importada requer revisão humana. Motivo obrigatório; o backend mantém os gates de aprovação.</p>}
      <div><label>Responsável pela revisão de voz <input value={reviewActor} disabled={blocked} onChange={event => editReview(() => setReviewActor(event.target.value))} /></label>
        <label>Motivo da revisão de voz <textarea value={reason} disabled={blocked} onChange={event => editReview(() => setReason(event.target.value))} /></label>
        <label><input type="checkbox" checked={listened} disabled={blocked} onChange={event => {setListened(event.target.checked); markDirty('review');}} />Ouvi o WAV processado e revisei os resultados</label>
        <label><input type="checkbox" checked={rejectionConfirmed} disabled={blocked} onChange={event => {setRejectionConfirmed(event.target.checked); markDirty('review');}} />Revisei os resultados para rejeitar</label>
        <button disabled={!canApprove} onClick={() => {void review('approve');}}>Enfileirar aprovação de voz</button>
        <button disabled={!canReject} onClick={() => {void review('reject');}}>Enfileirar rejeição de voz</button>
      </div></div>}
    {reviewNotice && <p role="status">{reviewNotice}</p>}
  </section>;
}

function VoiceOperationMonitor({campaignId, jobId, kind, voiceId, onChange, onComplete}: {campaignId: string; jobId: string; kind: 'voice_import' | 'voice_review' | null;
  voiceId?: string; onChange: () => void; onComplete: () => void}) {
  const operation = usePolling(`${campaignId}:voice-operation:${jobId}`, signal => read<VoiceOperationView>(campaignPath(campaignId, `/operations/${encodeURIComponent(jobId)}`), signal,
    value => voiceOperationView(value) && value.campaignId === campaignId && value.jobId === jobId && (kind === null || value.operation === kind)
      && (!voiceId || !value.result || value.result.voiceMasterId === voiceId)), 2000);
  useEffect(() => { if (operation.data && !operation.error) onChange(); }, [operation.data?.status, operation.error, onChange]);
  useEffect(() => { if (operation.data?.status === 'completed' && operation.data.operation === 'voice_review' && !operation.error) onComplete(); },
    [operation.data?.status, operation.data?.operation, operation.error, onComplete]);
  const result = operation.data?.status === 'completed' ? operation.data.result : null;
  return <div>
    {operation.error && <p role="alert">{operation.error.message} ({operation.error.code}). Dados desatualizados.</p>}
    {operation.data && <><p>Operação {jobId}: {operation.data.operation} · {operation.data.status}</p><p>Erro: {operation.data.errorCode ?? 'Não disponível'}</p></>}
    {!result && !operation.error && <p>Fase 1: executar operação local (local_operations)</p>}
    {result?.operation === 'voice_import' && <><p>Fase 2: processar voz importada</p><p>Voice Master importado: {result.voiceMasterId}.
      {operation.error ? ' Contexto da operação desatualizado; último resultado válido preservado.' : ' Inicie voice_import explicitamente.'}</p>
      <VoiceJobMonitor key={result.jobId} campaignId={campaignId} jobId={result.jobId} kind="voice.import" onChange={onChange} /></>}
    {result?.operation === 'voice_review' && <p>Review executado para {result.voiceMasterId}. Consulte a voz persistida; o resultado sozinho não confirma aprovação nem autoria.</p>}
    <button onClick={operation.refresh}>Consultar operação de voz</button>
  </div>;
}

function VoiceJobMonitor({campaignId, jobId, kind, onChange}: {campaignId: string; jobId: string; kind: string; onChange: () => void}) {
  const job = usePolling(`${campaignId}:voice-job:${jobId}`, signal => read<JobSummary>(campaignPath(campaignId, `/jobs/${encodeURIComponent(jobId)}`), signal,
    value => jobSummary(value) && object(value) && value.jobId === jobId && value.campaignId === campaignId && value.jobType === kind), 2000);
  useEffect(() => { if (job.data && !job.error) onChange(); }, [job.data?.status, job.data?.completedAt, job.error, onChange]);
  return <div>{job.error && <p role="alert">{job.error.message} ({job.error.code}). Dados desatualizados.</p>}
    {job.data && <><p>{job.data.jobType} · {job.data.status}</p><p>Job: {jobId} · Tentativas {job.data.attemptCount}/{job.data.maxAttempts}</p>
      <p>Erro: {job.data.lastErrorCode ?? 'Não disponível'}</p></>}
    <button onClick={job.refresh}>Consultar Job de voz</button></div>;
}
