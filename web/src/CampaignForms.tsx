import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, campaignDetail, campaignPath, post, read } from './api';
import type { CampaignDetail, CopyMaster } from './api';
import type { RemoteState } from './usePolling';
import { useUnsavedChanges } from './useUnsavedChanges';

export type CopyFields = { headline: string; hook: string; body: string; cta: string };
const blankCopy: CopyFields = { headline: '', hook: '', body: '', cta: '' };
const idPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export function buildSourceText(copy: CopyFields): string {
  return `Headline:\n${copy.headline.trim()}\n\nHook:\n${copy.hook.trim()}\n\nBody:\n${copy.body.trim()}\n\nCTA:\n${copy.cta.trim()}`;
}
function approvedCopy(copy: CopyFields, actor: string) {
  return { headline: copy.headline.trim(), hook: copy.hook.trim(), body: copy.body.trim(), cta: copy.cta.trim(),
    sourceText: buildSourceText(copy), approvalState: 'approved' as const, approvedBy: actor.trim() };
}
function matchesCopy(value: CopyMaster, copy: ReturnType<typeof approvedCopy>): boolean {
  return value.approvalState === 'approved' && value.approvedBy === copy.approvedBy
    && value.headline === copy.headline && value.hook === copy.hook && value.body === copy.body
    && value.cta === copy.cta && value.sourceText === copy.sourceText;
}
function CopyInputs({ value, onChange, actor, setActor, approved, setApproved }: {
  value: CopyFields; onChange: (value: CopyFields) => void; actor: string; setActor: (value: string) => void;
  approved: boolean; setApproved: (value: boolean) => void;
}) {
  return <>
    {(['headline', 'hook', 'body', 'cta'] as const).map((key, index) => <label key={key}>{['Headline', 'Hook', 'Body', 'CTA'][index]}
      <textarea required value={value[key]} onChange={event => onChange({ ...value, [key]: event.target.value })} /></label>)}
    <label>Aprovado por<input required value={actor} onChange={event => setActor(event.target.value)} /></label>
    <label className="checkbox-label"><input type="checkbox" checked={approved} onChange={event => setApproved(event.target.checked)} />Confirmo a aprovação da copy</label>
    <p className="muted">A headline é visual. Somente hook, body e CTA compõem o texto falado.</p>
  </>;
}
type SceneDraft = { variantId: string; location: string; timeAtmosphere: string; action: string; prompt: string; proofObject: string };
const blankScene: SceneDraft = { variantId: '', location: '', timeAtmosphere: '', action: '', prompt: '', proofObject: '' };

export function CampaignCreateForm({ onCreated }: { onCreated: (campaignId: string) => void }) {
  const [campaignId, setId] = useState(''), [character, setCharacter] = useState('susan-smith');
  const [proofObject, setProof] = useState(''), [voicePreset, setVoice] = useState(''), [editPreset, setEdit] = useState('');
  const [copy, setCopy] = useState<CopyFields>(blankCopy), [actor, setActor] = useState(''), [approved, setApproved] = useState(false);
  const [scenes, setScenes] = useState<SceneDraft[]>([{ ...blankScene }]);
  const [dirty, setDirty] = useState(false), [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  const [intent, setIntent] = useState<ReturnType<typeof payload> | null>(null);
  const alive = useRef(false), lock = useRef(false);
  const clearDirty = useUnsavedChanges(dirty);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  function payload() {
    return { campaignId: campaignId.trim(), character, proofObject: proofObject.trim(), voicePreset: voicePreset.trim(), editPreset: editPreset.trim(),
      status: 'draft', budget: {}, config: {}, copyMaster: approvedCopy(copy, actor),
      sceneVariants: scenes.map(scene => ({ variantId: scene.variantId.trim(), location: scene.location.trim(), action: scene.action.trim(), prompt: scene.prompt.trim(),
        timeAtmosphere: scene.timeAtmosphere.trim() || null, proofObject: scene.proofObject.trim() || null, status: 'not_started' })) };
  }
  function matches(value: unknown, body: ReturnType<typeof payload>): value is CampaignDetail {
    if (!campaignDetail(value)) return false;
    const result = value as CampaignDetail;
    return result.campaignId === body.campaignId && result.character === body.character && result.proofObject === body.proofObject
      && result.voicePreset === body.voicePreset && result.editPreset === body.editPreset
      && result.copyMasters.some(item => matchesCopy(item, body.copyMaster))
      && result.sceneVariants.length === body.sceneVariants.length && body.sceneVariants.every(scene => result.sceneVariants.some(
        item => item.variantId === scene.variantId && item.location === scene.location && item.action === scene.action && item.prompt === scene.prompt
          && item.timeAtmosphere === scene.timeAtmosphere && item.proofObject === scene.proofObject));
  }
  const finish = (id: string) => { clearDirty(); setDirty(false); setApproved(false); setIntent(null); onCreated(id); };
  const send = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (lock.current || intent) return;
    const body = payload();
    if (!approved || !actor.trim() || !idPattern.test(body.campaignId) || !proofObject.trim() || !voicePreset.trim() || !editPreset.trim()
      || Object.values(copy).some(value => !value.trim()) || scenes.some(scene => !idPattern.test(scene.variantId.trim()) || !scene.location.trim() || !scene.action.trim() || !scene.prompt.trim())) {
      setNotice('Preencha os campos obrigatórios e confirme a aprovação da copy.');
      event.currentTarget.querySelector<HTMLElement>('input:invalid, textarea:invalid')?.focus(); return;
    }
    const ids = body.sceneVariants.map(scene => scene.variantId), locations = body.sceneVariants.map(scene => scene.location.toLowerCase().split(/\s+/).join(' '));
    if (new Set(ids).size !== ids.length || new Set(locations).size !== locations.length) { setNotice('IDs de variantes e locais devem ser distintos.'); return; }
    lock.current = true; setBusy(true); setNotice(null);
    try {
      const result = await post<unknown>('/api/v1/campaigns', body);
      if (!alive.current) return;
      if (!matches(result, body)) throw new ApiError('command_unknown');
      finish(body.campaignId);
    } catch (error) {
      if (alive.current) {
        const safe = error instanceof ApiError ? error : new ApiError('command_unknown');
        setNotice(`${safe.message} (${safe.code})`);
        if (safe.code === 'command_unknown') setIntent(body);
      }
    } finally { if (alive.current) { setBusy(false); lock.current = false; } }
  };
  const reconcile = async () => {
    if (!intent || lock.current) return;
    lock.current = true; setBusy(true);
    try {
      const result = await read<CampaignDetail>(campaignPath(intent.campaignId), new AbortController().signal, campaignDetail);
      if (alive.current) {
        if (matches(result, intent)) finish(intent.campaignId);
        else setNotice('Criação não confirmada. Inspecione a campanha antes de decidir outra ação.');
      }
    } catch (error) { if (alive.current) setNotice(error instanceof ApiError ? error.message : 'Criação não confirmada.'); }
    finally { if (alive.current) { setBusy(false); lock.current = false; } }
  };
  return <form aria-label="Criar campanha" onSubmit={send} onChange={() => setDirty(true)}>
    <fieldset disabled={busy || intent !== null}>
      <legend>Campanha e cenas iniciais</legend>
      <label>ID da campanha<input required pattern="[a-z0-9]+(-[a-z0-9]+)*" value={campaignId} onChange={event => setId(event.target.value)} /></label>
      <label>Personagem<select value={character} onChange={event => setCharacter(event.target.value)}><option value="susan-smith">Susan Smith</option><option value="soul-constellation">Soul Constellation</option></select></label>
      <label>Objeto de prova<input required value={proofObject} onChange={event => setProof(event.target.value)} /></label>
      <label>Voice preset<input required value={voicePreset} onChange={event => setVoice(event.target.value)} /></label>
      <label>Edit preset<input required value={editPreset} onChange={event => setEdit(event.target.value)} /></label>
      <CopyInputs value={copy} onChange={setCopy} actor={actor} setActor={setActor} approved={approved} setApproved={setApproved} />
      {scenes.map((scene, index) => <fieldset key={index}><legend>Cena {index + 1}</legend>
        {(Object.entries({ variantId: 'ID da variante', location: 'Localização', timeAtmosphere: 'Atmosfera', action: 'Ação', prompt: 'Prompt', proofObject: 'Objeto de prova da cena' }) as [keyof SceneDraft, string][]).map(([key, label]) => <label key={key}>{label} {index + 1}
          <input required={!['timeAtmosphere', 'proofObject'].includes(key)} value={scene[key]} onChange={event => setScenes(previous => previous.map((item, i) => i === index ? { ...item, [key]: event.target.value } : item))} /></label>)}
        {scenes.length > 1 && <button type="button" onClick={() => { setScenes(previous => previous.filter((_, i) => i !== index)); setDirty(true); }}>Remover cena {index + 1}</button>}
      </fieldset>)}
      <button type="button" onClick={() => { setScenes(previous => [...previous, { ...blankScene }]); setDirty(true); }}>Adicionar cena</button>
      <div className="actions"><button disabled={busy || intent !== null || !approved || !actor.trim()} type="submit">Criar campanha com copy aprovada</button></div>
    </fieldset>
    <p className="muted">Rascunhos não salvos se perdem ao sair. Presets são referências; nenhum gasto é autorizado aqui.</p>
    {notice && <p role="alert">{notice}</p>}
    {intent && <button type="button" disabled={busy} onClick={() => { void reconcile(); }}>Reconciliar criação</button>}
  </form>;
}

export function CopyVersionForm({ campaignId, detail }: { campaignId: string; detail: RemoteState<CampaignDetail> }) {
  const [copy, setCopy] = useState<CopyFields>(blankCopy), [actor, setActor] = useState(''), [approved, setApproved] = useState(false);
  const [dirty, setDirty] = useState(false), [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<{ body: ReturnType<typeof approvedCopy>; oldIds: string[]; minimumReadId: number } | null>(null);
  const generation = useRef(0), lock = useRef(false), identity = useRef(campaignId);
  identity.current = campaignId;
  const clearDirty = useUnsavedChanges(dirty);
  useEffect(() => {
    generation.current++; lock.current = false;
    setPending(null); setBusy(false); setNotice(null); setApproved(false); setCopy(blankCopy); setActor(''); setDirty(false);
    return () => { generation.current++; };
  }, [campaignId]);
  useEffect(() => {
    if (!pending || detail.error || detail.data?.campaignId !== campaignId || detail.lastSuccessReadId === null || detail.lastSuccessReadId < pending.minimumReadId) return;
    const matches = detail.data.copyMasters.filter(item => !pending.oldIds.includes(item.copyMasterId) && matchesCopy(item, pending.body));
    if (matches.length === 1) { setPending(null); setNotice('Versão de copy registrada. Histórico preservado.'); setDirty(false); clearDirty(); }
    else if (matches.length > 1) setNotice('Resultado desconhecido: múltiplas versões correspondem. Inspecione o histórico.');
  }, [pending, detail.data, detail.error, detail.lastSuccessReadId, campaignId, clearDirty]);
  const connected = detail.data?.campaignId === campaignId && !detail.error;
  const send = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (lock.current || pending || !connected) return;
    if (!approved || !actor.trim() || Object.values(copy).some(value => !value.trim())) { setNotice('Preencha os campos obrigatórios e confirme a aprovação da copy.'); return; }
    const body = approvedCopy(copy, actor), oldIds = detail.data!.copyMasters.map(item => item.copyMasterId), scope = generation.current;
    lock.current = true; setBusy(true); setNotice(null); let reconcile = false;
    try {
      const result = await post<unknown>(campaignPath(campaignId, '/copies'), body);
      if (scope !== generation.current || identity.current !== campaignId) return;
      if (!campaignDetail(result) || (result as CampaignDetail).campaignId !== campaignId || !(result as CampaignDetail).copyMasters.some(item => matchesCopy(item, body))) throw new ApiError('command_unknown');
      reconcile = true; setNotice('Copy recebida. Consultando o histórico.');
    } catch (error) {
      if (scope === generation.current && identity.current === campaignId) {
        const safe = error instanceof ApiError ? error : new ApiError('command_unknown');
        setNotice(`${safe.message} (${safe.code})`); reconcile = safe.code === 'command_unknown';
      }
    } finally {
      if (scope === generation.current && identity.current === campaignId) {
        const minimumReadId = detail.refresh(); if (reconcile) setPending({ body, oldIds, minimumReadId });
        setBusy(false); setApproved(false); lock.current = false;
      }
    }
  };
  return <form aria-label="Nova versão de copy" onSubmit={send} onChange={() => setDirty(true)}>
    <fieldset disabled={!connected || busy || pending !== null}><legend>Copy aprovada</legend>
      <CopyInputs value={copy} onChange={setCopy} actor={actor} setActor={setActor} approved={approved} setApproved={setApproved} />
      <button type="submit" disabled={!approved || !actor.trim()}>Adicionar versão de copy aprovada</button>
    </fieldset>
    <p className="muted">Rascunhos não salvos se perdem ao sair. As versões anteriores não serão alteradas.</p>
    {notice && <p role="status">{notice}</p>}
    {pending && <button type="button" onClick={detail.refresh}>Consultar copy novamente</button>}
  </form>;
}
