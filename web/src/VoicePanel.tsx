import { useEffect, useRef, useState } from 'react';
import { ApiError, campaignPath, jobSummary, object, post, read } from './api';
import type { CampaignDetail, Items, JobSummary, VoiceSummary } from './api';
import { campaignBudgetView, voiceSubmission } from './voiceApi';
import type { CampaignBudgetView } from './voiceApi';
import { usePolling } from './usePolling';
import type { RemoteState } from './usePolling';
import { useUnsavedChanges } from './useUnsavedChanges';

type Props = {campaignId: string; detail: RemoteState<CampaignDetail>; voices: RemoteState<Items<VoiceSummary>>; jobs: RemoteState<Items<JobSummary>>};
const positive = (value: string) => /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) > 0;
const actorValid = (value: string) => /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,119}$/.test(value);

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
  const [dirty, setDirty] = useState(false);
  const clearDirty = useUnsavedChanges(dirty || unknown || busy);
  const alive = useRef(true), lock = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const budget = usePolling(`${campaignId}:budget`, signal => read<CampaignBudgetView>(campaignPath(campaignId, '/budget'), signal, campaignBudgetView), null);
  const connected = Boolean(detail.data && voices.data && jobs.data && !detail.error && !voices.error && !jobs.error
    && detail.data.campaignId === campaignId && voices.data.items.every(voice => voice.campaignId === campaignId));
  const budgetFresh = budget.data && !budget.error;
  const blocked = !connected || busy || unknown || budgetIntent !== null;
  const copy = detail.data?.copyMasters.find(item => item.version === Number(version) && item.approvalState === 'approved');
  const edit = (action: () => void) => { action(); setBudgetConfirmed(false); setPaid(false); setDirty(true); };
  const canGenerate = !blocked && budgetFresh && budget.data?.state === 'configured' && copy
    && /^[A-Za-z0-9_-]{1,120}$/.test(voiceId) && /^[A-Za-z0-9_-]{1,120}$/.test(modelId)
    && actorValid(actor) && positive(ceiling) && Number(ceiling) <= budget.data.limitCents && paid;

  useEffect(() => {
    if (!budgetIntent || budget.error || (budget.lastSuccessReadId ?? 0) < budgetIntent.minimumRead) return;
    if (budget.data?.state === 'configured') {
      setNotice(budget.data.currency === budgetIntent.currency && budget.data.limitCents === budgetIntent.limitCents
        ? 'Configuração solicitada observada. Isso não autoriza geração nem informa autoria.' : 'Outro orçamento foi observado. Não será sobrescrito.');
      setBudgetIntent(null); setDirty(false); clearDirty();
    }
  }, [budget.data, budget.error, budget.lastSuccessReadId, budgetIntent, clearDirty]);

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
      setSelectedJob(result.jobId); setAcceptedVoice(result.voiceMasterId!); setDirty(false); clearDirty();
      setNotice('Job na fila. Inicie voice_generate explicitamente; isso não aprova o áudio.');
    } catch (error) {
      if (!alive.current) return;
      const failure = error instanceof ApiError ? error : new ApiError('command_unknown');
      setUnknown(failure.code === 'command_unknown'); setNotice(`${failure.code}: ${failure.message}`);
    } finally {
      if (alive.current) { jobs.refresh(); voices.refresh(); setBusy(false); lock.current = false; }
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
          <label>Moeda da campanha <input value={currency} maxLength={3} onChange={event => edit(() => setCurrency(event.target.value))} /></label>
          <label>Limite da campanha (centavos) <input type="number" min="1" step="1" value={limit} onChange={event => edit(() => setLimit(event.target.value))} /></label>
          <label><input type="checkbox" checked={budgetConfirmed} onChange={event => setBudgetConfirmed(event.target.checked)} />Confirmo este orçamento inicial</label>
          <button disabled={!budgetConfirmed || !/^[A-Z]{3}$/.test(currency) || !positive(limit)}>Configurar orçamento inicial</button>
        </fieldset>
      </form>}
      <p>Configuração inicial somente: moeda/limite definidos não são editáveis aqui. Não é saldo disponível nem estimativa de preço e não autoriza gasto.</p>
      <button type="button" onClick={budget.refresh}>Consultar orçamento</button>
    </section>
    <label>Versão da copy para voz <select value={version} disabled={busy || unknown} onChange={event => edit(() => setVersion(event.target.value))}>
      <option value="">Selecione a versão aprovada</option>{detail.data?.copyMasters.filter(item => item.approvalState === 'approved').map(item => <option key={item.copyMasterId} value={item.version}>v{item.version}</option>)}
    </select></label>
    {copy && <div><p>Headline visual (não narrada): {copy.headline}</p><p>Texto narrado: {copy.hook}{'\n\n'}{copy.body}{'\n\n'}{copy.cta}</p></div>}
    <form aria-label="Geração de voz" onSubmit={event => {event.preventDefault(); void generate();}}><fieldset disabled={blocked}>
      <label>Voice ID <input value={voiceId} onChange={event => edit(() => setVoiceId(event.target.value))} /></label>
      <label>Model ID <input value={modelId} onChange={event => edit(() => setModelId(event.target.value))} /></label>
      <label>Responsável pela geração <input value={actor} onChange={event => edit(() => setActor(event.target.value))} /></label>
      <label>Teto desta geração (centavos) <input type="number" min="1" step="1" value={ceiling} onChange={event => edit(() => setCeiling(event.target.value))} /></label>
      <label><input type="checkbox" checked={paid} onChange={event => setPaid(event.target.checked)} />Autorizo o gasto desta geração</label>
      <button disabled={!canGenerate}>Enfileirar geração de voz</button>
    </fieldset></form>
    <p>IDs do ElevenLabs são informados manualmente. A mesma identidade pode reutilizar voz/Job; não há regeneração forçada.</p>
    {notice && <p role="status">{notice}</p>}
    {unknown && <p>Intenção anterior preservada, sem reenvio. Inspecione Jobs e vozes persistidos; coincidência de versão/provider não prova identidade.</p>}
    <label>Job de geração para inspecionar <select value={selectedJob} disabled={!connected || busy} onChange={event => {setSelectedJob(event.target.value); setAcceptedVoice(null);}}>
      <option value="">Selecione um Job</option>{selectedJob && !jobs.data?.items.some(item => item.jobId === selectedJob) && <option value={selectedJob}>{selectedJob}</option>}
      {jobs.data?.items.filter(item => item.campaignId === campaignId && item.jobType === 'voice.generate').map(item => <option key={item.jobId} value={item.jobId}>{item.jobId}</option>)}
    </select></label>
    {acceptedVoice && <p>Voice Master aceito: {acceptedVoice}</p>}
    {selectedJob && <VoiceJobMonitor key={selectedJob} campaignId={campaignId} jobId={selectedJob} kind="voice.generate" onChange={voices.refresh} />}
  </section>;
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
