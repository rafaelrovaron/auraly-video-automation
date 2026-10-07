import { useEffect, useState } from 'react';
import { CampaignDetailPanel, CampaignList } from './CampaignPanel';
import { ProfilePanel } from './ProfilePanel';

type Route = { page: 'list' } | { page: 'detail'; campaignId: string } | { page: 'profiles' } | { page: 'invalid' };

export function parseRoute(hash: string): Route {
  if (hash === '' || hash === '#/campaigns') return { page: 'list' };
  if (hash === '#/profiles') return { page: 'profiles' };
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
    <header><a href="#/campaigns" className="brand">Auraly <span>painel local</span></a>
      <nav aria-label="Navegação principal"><a href="#/campaigns">Campanhas</a> · <a href="#/profiles">Profiles de edição</a></nav></header>
    <main id="main">
      {route.page === 'profiles' ? <ProfilePanel /> : route.page === 'list' ? <CampaignList /> : route.page === 'detail' ?
        <CampaignDetailPanel key={route.campaignId} campaignId={route.campaignId} />
        : <><h1>Rota inválida</h1><a href="#/campaigns">Voltar às campanhas</a></>}
    </main>
  </>;
}
