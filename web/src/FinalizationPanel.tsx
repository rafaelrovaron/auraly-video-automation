import { useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
import { getEditPlan, listEditPlans } from './editingApi';
import type { EditBatchPlan, PlanSummary } from './editingApi';
import { RenderPanel } from './RenderPanel';

type Props = { campaignId: string; active: boolean; preferredPlan: EditBatchPlan | null };
const identity = ({ videoId, planHash }: { videoId: string; planHash: string }) => `${videoId}/${planHash}`;

export function FinalizationPanel({ campaignId, active, preferredPlan }: Props) {
  const [summaries, setSummaries] = useState<PlanSummary[]>([]);
  const [selected, setSelected] = useState('');
  const [plan, setPlan] = useState<EditBatchPlan | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [renderBusy, setRenderBusy] = useState(false);
  const selection = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void listEditPlans(campaignId, controller.signal)
      .then(items => { if (!controller.signal.aborted) { setSummaries(items); setNotice(null); } })
      .catch(error => { if (!controller.signal.aborted) setNotice((error instanceof ApiError ? error : new ApiError('connection_lost')).message); });
    return () => controller.abort();
  }, [active, campaignId, preferredPlan?.planHash]);

  useEffect(() => {
    if (preferredPlan && !renderBusy) { selection.current?.abort(); setLoading(false); setSelected(identity(preferredPlan)); setPlan(preferredPlan); }
  }, [preferredPlan]);

  useEffect(() => () => selection.current?.abort(), []);

  const choose = async (key: string) => {
    if (renderBusy) return;
    selection.current?.abort();
    setSelected(key); setPlan(null); setNotice(null); setLoading(false);
    const summary = summaries.find(item => identity(item) === key);
    if (!summary) return;
    const controller = new AbortController(); selection.current = controller; setLoading(true);
    try {
      const found = await getEditPlan(campaignId, summary.videoId, summary.planHash, controller.signal);
      if (!controller.signal.aborted) setPlan(found);
    } catch (error) {
      if (!controller.signal.aborted) setNotice((error instanceof ApiError ? error : new ApiError('connection_lost')).message);
    } finally { if (!controller.signal.aborted) setLoading(false); }
  };

  const options = preferredPlan && !summaries.some(item => identity(item) === identity(preferredPlan))
    ? [{ videoId: preferredPlan.videoId, planHash: preferredPlan.planHash, outputCount: preferredPlan.outputCount,
      source: preferredPlan.source }, ...summaries]
    : summaries;
  const description = (item: typeof options[number]) => {
    const source = item.source.path.split(/[\\/]/).at(-1) || item.videoId;
    return `${source} · ${item.outputCount} ${item.outputCount === 1 ? 'variação' : 'variações'}`;
  };
  return <>
    <p>Escolha uma configuração salva para gerar e revisar os vídeos finais.</p>
    <label>Plano salvo para finalizar
      <select value={selected} disabled={renderBusy} onChange={event => { void choose(event.target.value); }}>
        <option value="">Selecione</option>
        {options.map(item => <option key={identity(item)} value={identity(item)}>
          {description(item)}{options.filter(other => description(other) === description(item)).length > 1 ? ` · ${item.planHash.slice(0, 8)}` : ''}
        </option>)}
      </select>
    </label>
    {loading && <p role="status">Consultando configuração salva…</p>}
    {notice && <p role="status">{notice}</p>}
    <RenderPanel campaignId={campaignId} plan={plan} onBusyChange={setRenderBusy} />
  </>;
}
