import { useEffect, useState } from 'react';
import { ApiError, collection, pendingLabel, read, statusLabel } from './api';
import type { CampaignSummary, Items } from './api';

export function CampaignList() {
  const [data, setData] = useState<Items<CampaignSummary> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    read<Items<CampaignSummary>>('/api/v1/campaigns', controller.signal, collection)
      .then(value => { if (!controller.signal.aborted) { setData(value); setError(null); } })
      .catch(e => { if (!controller.signal.aborted) setError(e); });
    return () => controller.abort();
  }, [revision]);
  return <>
    <div className="section-heading"><h1>Campanhas</h1><button onClick={() => setRevision(value => value + 1)}>Atualizar</button></div>
    <p>Consulte o andamento da produção. Nenhuma ação paga é iniciada ao abrir o painel.</p>
    {error && <p role="alert">{error.message} ({error.code})</p>}
    {!data && !error && <p role="status">Carregando campanhas…</p>}
    {data?.items.length === 0 && <p>Nenhuma campanha. Crie uma pelo fluxo existente da CLI.</p>}
    <div className="campaign-list">{data?.items.map(item => <article key={item.campaignId}>
      <h2><a href={`#/campaigns/${encodeURIComponent(item.campaignId)}`}>{item.campaignId}</a></h2>
      <p>{item.character}</p><p>{item.sceneCount} cenas · {statusLabel(item.operationalStatus)}</p>
      <p>{item.nextPending ? pendingLabel(item.nextPending) : 'Sem pendência informada'}</p>
    </article>)}</div>
  </>;
}
