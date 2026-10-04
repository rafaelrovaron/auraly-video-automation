import { useEffect, useRef, useState } from 'react';
import { ApiError, campaignPath, object, post, read, sceneImages, statusLabel } from './api';
import type { CampaignDetail, ImageSummary, Items, JobSummary, SceneImages } from './api';
import { diagnosticLabel, folderOf, imageOperationView, relativeImagePath } from './imageImportApi';
import type { Association, ImageImportResult, ImageManifestResult, ImageOperationView } from './imageImportApi';
import { usePolling } from './usePolling';
import type { RemoteState } from './usePolling';
import { useUnsavedChanges } from './useUnsavedChanges';

type Props = { campaignId: string; detail: RemoteState<CampaignDetail>; images: RemoteState<Items<SceneImages>>; jobs: RemoteState<Items<JobSummary>> };
type Intent = { operation: string; body: Record<string, unknown>; signature: string; jobId: string | null; minimumRead: number; minimumJobsRead: number };
const normalize = (value: string) => value.trim().replaceAll('\\', '/');
const associations = (items: Association[]) => JSON.stringify([...items].sort((a, b) => a.variantId.localeCompare(b.variantId)));

export function ImageImportPanel({ campaignId, detail, images, jobs }: Props) {
  const scenes = detail.data?.sceneVariants ?? [];
  const [output, setOutput] = useState('');
  const [directory, setDirectory] = useState('');
  const [paths, setPaths] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<ImageManifestResult | null>(null);
  const [diagnostic, setDiagnostic] = useState<ImageImportResult | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [dirty, setDirty] = useState(false);
  const clearDirty = useUnsavedChanges(dirty);
  const [jobId, setJobId] = useState('');
  const [intent, setIntent] = useState<Intent | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const lock = useRef(false);
  const identity = useRef({ campaignId, generation: 0, alive: true });
  if (identity.current.campaignId !== campaignId) identity.current = { campaignId, generation: identity.current.generation + 1, alive: true };
  useEffect(() => { identity.current.alive = true; return () => { identity.current.alive = false; }; }, []);
  const rows = scenes.map(scene => ({ variantId: scene.variantId, path: normalize(paths[scene.variantId] ?? '') }));
  const signature = JSON.stringify([campaignId, normalize(output), normalize(directory), associations(rows)]);
  const connected = Boolean(detail.data && images.data && jobs.data && !detail.error && !images.error && !jobs.error);
  const operation = usePolling(`${campaignId}:image:${jobId}`, signal => jobId ? read<ImageOperationView>(
    campaignPath(campaignId, `/operations/${encodeURIComponent(jobId)}`), signal,
    value => imageOperationView(value) && value.campaignId === campaignId && value.jobId === jobId) : Promise.resolve(null), jobId ? 2000 : null);
  const edit = (change: () => void) => { change(); setSaved(null); setDiagnostic(null); setConfirmed(false); setDirty(true); setNotice(''); };
  const completeRows = rows.length > 0 && rows.every(row => relativeImagePath(row.path));
  const coverage = (items: { variantId: string }[]) => items.length === scenes.length && scenes.every(scene => items.some(item => item.variantId === scene.variantId));

  const submit = async (kind: string, suffix: string, body: Record<string, unknown>) => {
    if (lock.current || intent || !connected) return;
    lock.current = true; setBusy(true); setNotice(''); setConfirmed(false);
    const generation = identity.current.generation;
    const pending: Intent = { operation: kind, body, signature, jobId: null, minimumRead: 0, minimumJobsRead: 0 };
    const current = () => identity.current.alive && identity.current.generation === generation;
    let accepted = false;
    try {
      const value = await post<unknown>(campaignPath(campaignId, suffix), { campaignId, operation: kind, ...body });
      if (!current()) return;
      if (!object(value) || value.campaignId !== campaignId || value.operation !== kind || typeof value.jobId !== 'string' || !value.jobId) throw new ApiError('command_unknown');
      pending.jobId = value.jobId; accepted = true;
      setNotice('Job na fila. Inicie o worker Operações locais explicitamente, se estiver parado.');
    } catch (error) {
      if (!current()) return;
      const failure = error instanceof ApiError ? error : new ApiError('command_unknown');
      setNotice(`${failure.code}: ${failure.message}`);
      if (failure.code !== 'command_unknown') return;
    } finally {
      if (current()) {
        pending.minimumJobsRead = jobs.refresh();
        pending.minimumRead = operation.refresh();
        if (accepted) setJobId(pending.jobId!);
        setBusy(false); lock.current = false;
      }
    }
    if (current()) setIntent(pending);
  };

  useEffect(() => {
    const view = operation.data;
    if (!intent || !view || busy || operation.error || (operation.lastSuccessReadId ?? 0) < intent.minimumRead
      || (intent.jobId !== null && view.jobId !== intent.jobId) || view.operation !== intent.operation) return;
    if (view.status === 'failed' || view.status === 'cancelled') {
      if (intent.jobId === null) return; // A failed unrelated job cannot reconcile a lost submission.
      setNotice(`Job ${statusLabel(view.status)} (${view.errorCode ?? 'operation_not_allowed'}). Consulte o job antes de uma nova ação.`);
      setIntent(null); setDiagnostic(null); return;
    }
    if (view.status !== 'completed' || !view.result) return;
    if (intent.signature !== signature) { setIntent(null); setNotice('As associações mudaram. O resultado anterior não foi aplicado.'); return; }
    const result = view.result;
    if (result.operation === 'image_prepare') {
      const requestedOutput = normalize(String(intent.body.outputPath));
      if (result.variantCount !== scenes.length || !folderOf(result.manifestPath).endsWith(`/${requestedOutput}`)
        || (result.outputPath == null ? intent.jobId === null : result.outputPath !== requestedOutput)) return;
      setDirectory(folderOf(result.manifestPath)); setSaved(null); setDiagnostic(null); setDirty(true);
      setNotice('Pasta preparada. Copie as imagens no Explorer e preencha uma associação por cena.');
    } else if (result.operation === 'image_manifest') {
      if (!coverage(result.items) || folderOf(result.manifestPath) !== normalize(directory) || associations(result.items) !== associations(rows)) return;
      setSaved(result); setDiagnostic(null); setDirty(false); clearDirty(); setNotice('Associações salvas. Valide o batch antes de importar.');
    } else {
      if (!saved || intent.body.manifestSha256 !== saved.manifestSha256 || result.mode !== intent.body.mode) return;
      if (result.mode === 'dry_run') {
        if (result.manifestSha256 !== saved.manifestSha256 || result.validationId !== intent.body.validationId || result.total !== scenes.length
          || (result.valid === true && (!coverage(result.items) || result.items.some(item => scenes.find(scene => scene.variantId === item.variantId)?.sceneVariantId !== item.sceneVariantId)))) return;
        setDiagnostic(result); setNotice('');
      } else {
        if (intent.jobId === null && result.manifestSha256 !== saved.manifestSha256) return;
        if (result.manifestSha256 !== null && result.manifestSha256 !== saved.manifestSha256) return;
        if (!coverage(result.items) || result.items.some(item => !item.imageCandidateId)) return;
        setDiagnostic(null); setNotice('Importação concluída. Revise os candidatos antes de aprovar.'); images.refresh(); detail.refresh();
      }
    }
    setConfirmed(false); setIntent(null); jobs.refresh();
  }, [operation.data, operation.error, operation.lastSuccessReadId, intent, busy, signature]);

  const inspected = !operation.error && operation.data?.status === 'completed' ? operation.data.result : null;
  const canRecover = inspected?.operation === 'image_manifest' && coverage(inspected.items) && (!intent
    || (intent.operation === 'image_manifest' && intent.signature === signature && folderOf(inspected.manifestPath) === normalize(directory)
      && (operation.lastSuccessReadId ?? 0) >= intent.minimumRead && associations(inspected.items) === associations(rows)));
  const recover = () => {
    if (!canRecover || inspected?.operation !== 'image_manifest') return;
    setDirectory(folderOf(inspected.manifestPath)); setPaths(Object.fromEntries(inspected.items.map(row => [row.variantId, row.path])));
    setSaved(inspected); setDiagnostic(null); setConfirmed(false); setDirty(false); clearDirty(); setIntent(null);
    setNotice('Associações salvas recuperadas. Uma nova validação é obrigatória.');
  };
  const blocked = busy || Boolean(intent) || !connected;
  return <section aria-label="Importação manual de imagens">
    <h3>Batch manual de imagens</h3>
    <p>Prepare a pasta, copie seus arquivos pelo Explorer, associe cada cena e salve. Não há monitoramento automático da pasta nem upload no navegador.</p>
    {!connected && <p role="alert">Dados indisponíveis ou desatualizados. Atualize antes de enviar comandos.</p>}
    <label>Pasta de importação (relativa ao work root)<input value={output} disabled={busy || Boolean(intent?.jobId === null)} onChange={event => edit(() => setOutput(event.target.value))} placeholder="imports/campanha" /></label>
    <button disabled={blocked || !relativeImagePath(normalize(output))} onClick={() => void submit('image_prepare', '/images/import/prepare', { outputPath: normalize(output) })}>Preparar pasta</button>
    <label>Pasta preparada (relativa ao projeto)<input value={directory} disabled={busy || Boolean(intent?.jobId === null)} onChange={event => edit(() => setDirectory(event.target.value))} /></label>
    {directory && <p>Copie as imagens para: <code>{normalize(directory)}/images</code> (a partir da pasta do projeto). Não altere image-import.json.</p>}
    {scenes.map(scene => <fieldset key={scene.variantId}><legend>{scene.variantId} · {scene.location}</legend><p>{scene.action}</p><p>{scene.prompt}</p>
      <label>Arquivo de {scene.variantId}<input value={paths[scene.variantId] ?? ''} disabled={busy || Boolean(intent?.jobId === null)} placeholder="images/nome.png"
        onChange={event => edit(() => setPaths(previous => ({ ...previous, [scene.variantId]: event.target.value })))} /></label></fieldset>)}
    <div className="form-actions">
      <button disabled={blocked || !completeRows || !relativeImagePath(normalize(directory))} onClick={() => void submit('image_manifest', '/images/import/manifests', { directoryPath: normalize(directory), items: rows })}>Salvar associações</button>
      <button disabled={blocked || !saved} onClick={() => { setDiagnostic(null); void submit('image_import', '/images/import', { manifestPath: saved!.manifestPath,
        manifestSha256: saved!.manifestSha256, mode: 'dry_run', includeDiagnostics: true, validationId: crypto.randomUUID() }); }}>Validar batch</button>
    </div>
    {diagnostic && <div aria-live="polite"><h4>{diagnostic.valid === true ? 'Batch válido' : 'Validação concluída com erros'}</h4>
      {diagnostic.issues.map((issue, index) => <p key={index}>{issue.variantId && `${issue.variantId}: `}{diagnosticLabel(issue.code)}</p>)}
      {diagnostic.items.map(item => <p key={item.variantId}>{item.variantId} · {item.width} × {item.height} · {item.sizeBytes} bytes · {item.format} · SHA256 <code>{item.sha256}</code></p>)}
    </div>}
    <label className="checkbox"><input type="checkbox" checked={confirmed} disabled={blocked || diagnostic?.valid !== true} onChange={event => setConfirmed(event.target.checked)} />Confirmo a importação deste batch</label>
    <button disabled={blocked || !saved || diagnostic?.valid !== true || !confirmed} onClick={() => void submit('image_import', '/images/import', {
      manifestPath: saved!.manifestPath, manifestSha256: diagnostic!.manifestSha256, mode: 'execute',
      expectedSources: diagnostic!.items.map(({ variantId, sha256 }) => ({ variantId, sha256 })),
    })}>Importar batch validado</button>
    {notice && <p role="status">{notice}</p>}
    <details><summary>Consultar ou recuperar um job de imagens</summary>
      <label>Job de imagens<select value={jobId} disabled={busy || Boolean(intent && (jobs.lastSuccessReadId ?? 0) < intent.minimumJobsRead)} onChange={event => setJobId(event.target.value)}>
        <option value="">Selecione um job local</option>{jobs.data?.items.filter(job => job.jobType === 'api.local.operation').map(job => <option key={job.jobId} value={job.jobId}>{job.jobId} · {statusLabel(job.status)}</option>)}
      </select></label>
      {operation.error && <p role="alert">{operation.error.code}: {operation.error.message} {operation.data && 'Último resultado preservado, mas desatualizado.'}</p>}
      {operation.data && <p>Operação {operation.data.operation} · {statusLabel(operation.data.status)} · {operation.data.errorCode}</p>}
      <button disabled={!jobId || busy} onClick={() => { jobs.refresh(); operation.refresh(); }}>Consultar job novamente</button>
      <button disabled={!canRecover || busy || !connected} onClick={recover}>Usar associações salvas</button>
    </details>
    <ImageReview key={campaignId} campaignId={campaignId} images={images} />
  </section>;
}

function ImageReview({ campaignId, images }: { campaignId: string; images: RemoteState<Items<SceneImages>> }) {
  const [candidateId, setCandidateId] = useState(''); const [actor, setActor] = useState(''); const [action, setAction] = useState('approve');
  const [reason, setReason] = useState(''); const [confirmed, setConfirmed] = useState(false); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState('');
  const [pending, setPending] = useState<{ candidateId: string; status: string; minimumRead: number } | null>(null);
  const lock = useRef(false); const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const candidates = images.data?.items.flatMap(scene => scene.items) ?? [];
  useEffect(() => {
    if (pending && !images.error && (images.lastSuccessReadId ?? 0) >= pending.minimumRead
      && candidates.some(item => item.imageCandidateId === pending.candidateId && item.reviewStatus === pending.status)) {
      setPending(null); setNotice('Estado solicitado observado após a revisão. Esta consulta não informa autoria.');
    }
  }, [images.data, images.error, images.lastSuccessReadId, pending]);
  const submit = async () => {
    if (lock.current || pending) return;
    lock.current = true; setBusy(true); const target = candidateId; const status = action === 'reject' ? 'rejected' : 'approved';
    let unresolved = false;
    try {
      const value = await post<ImageSummary>(campaignPath(campaignId, `/images/${encodeURIComponent(target)}/review`), { campaignId, action, actor: actor.trim(), ...(reason.trim() ? { reason: reason.trim() } : {}) });
      if (!alive.current) return;
      if (!sceneImages({ sceneVariantId: value?.sceneVariantId, items: [value] }) || value.imageCandidateId !== target || value.reviewStatus !== status) throw new ApiError('command_unknown');
      unresolved = true; setNotice('Revisão recebida. Atualizando o estado observado.');
    } catch (error) {
      if (!alive.current) return;
      const failure = error instanceof ApiError ? error : new ApiError('command_unknown'); unresolved = failure.code === 'command_unknown'; setNotice(`${failure.code}: ${failure.message}`);
    } finally {
      if (alive.current) { const minimumRead = images.refresh(); if (unresolved) setPending({ candidateId: target, status, minimumRead }); setBusy(false); setConfirmed(false); lock.current = false; }
    }
  };
  return <div><h3>Revisão manual de imagem</h3>
    <label>Candidato de imagem<select value={candidateId} disabled={busy || Boolean(pending)} onChange={event => { setCandidateId(event.target.value); setConfirmed(false); }}><option value="">Selecione</option>
      {candidates.map(item => <option key={item.imageCandidateId} value={item.imageCandidateId}>{item.imageCandidateId} · {statusLabel(item.reviewStatus)}</option>)}</select></label>
    <label>Ação da revisão<select value={action} disabled={busy || Boolean(pending)} onChange={event => { setAction(event.target.value); setConfirmed(false); }}><option value="approve">Aprovar</option><option value="reject">Rejeitar</option><option value="replace">Substituir aprovada</option></select></label>
    <label>Ator da revisão<input value={actor} maxLength={120} disabled={busy || Boolean(pending)} onChange={event => { setActor(event.target.value); setConfirmed(false); }} /></label>
    <label>Motivo da revisão<input value={reason} maxLength={512} disabled={busy || Boolean(pending)} onChange={event => { setReason(event.target.value); setConfirmed(false); }} /></label>
    {action === 'replace' && <p>Substituir troca a imagem aprovada da cena. Confirme explicitamente esta decisão.</p>}
    <label className="checkbox"><input type="checkbox" checked={confirmed} disabled={busy || Boolean(pending)} onChange={event => setConfirmed(event.target.checked)} />Confirmo esta revisão de imagem</label>
    <button disabled={busy || Boolean(pending) || !candidateId || !candidates.some(item => item.imageCandidateId === candidateId) || !actor.trim() || !confirmed || (action === 'reject' && !reason.trim()) || Boolean(images.error) || !images.data} onClick={() => void submit()}>Aplicar revisão</button>
    {pending && <button onClick={() => setPending(previous => previous && { ...previous, minimumRead: images.refresh() })}>Consultar imagens novamente</button>}
    {notice && <p role="status">{notice}</p>}
  </div>;
}
