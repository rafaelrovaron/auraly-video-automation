import { useEffect, useState } from 'react';
import { CampaignList } from './CampaignPanel';

type Route = { page: 'list' } | { page: 'detail'; campaignId: string } | { page: 'invalid' };

export function parseRoute(hash: string): Route {
  if (hash === '' || hash === '#/campaigns') return { page: 'list' };
  const match = /^#\/campaigns\/([^/]+)$/.exec(hash);
  if (match) {
    try {
      const campaignId = decodeURIComponent(match[1]);
      if (/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(campaignId)) return { page: 'detail', campaignId };
    } catch { /* malformed hashes never reach the API */ }
  }
  return { page: 'invalid' };
}

export function App() {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const navigate = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, []);
  return <>
    <header><a href="#/campaigns" className="brand">Auraly <span>painel local</span></a></header>
    <main id="main">
      {route.page === 'list' ? <CampaignList /> : route.page === 'detail' ? <>
        <a href="#/campaigns">← Campanhas</a><h1>{route.campaignId}</h1><p>Detalhe ainda indisponível.</p>
      </> : <><h1>Rota inválida</h1><a href="#/campaigns">Voltar às campanhas</a></>}
    </main>
  </>;
}
