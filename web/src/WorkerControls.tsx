import { useEffect, useRef, useState } from 'react';
import { ApiError, campaignPath, post } from './api';
import type { WorkerKind, WorkerObservation, WorkerState } from './api';
import type { RemoteState } from './usePolling';
export type WorkerControlsProps = { campaignId: string; worker: RemoteState<WorkerObservation>; connected: boolean; onRefresh: () => void; initialKind?: WorkerKind };
const kinds: [WorkerKind, string][] = [
  ['local_operations', 'Operações locais'], ['voice_generate', 'Gerar voz'], ['voice_import', 'Processar voz importada'],
  ['heygen_assets', 'Upload de assets HeyGen'], ['heygen_videos', 'Vídeos HeyGen'],
  ['editing_render', 'Renderizar planos salvos'],
];

export function WorkerControls({ campaignId, worker, connected, onRefresh, initialKind = 'local_operations' }: WorkerControlsProps) {
  const [kind, setKind] = useState<WorkerKind>(initialKind);
  const [confirmation, setConfirmation] = useState<{ campaignId: string; kind: WorkerKind } | null>(null);
  const [sending, setSending] = useState(false);
  const [waitingRead, setWaitingRead] = useState<{ minimumReadId: number } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeCode, setNoticeCode] = useState<string | null>(null);
  const identity = useRef(campaignId);
  const generation = useRef(0);
  identity.current = campaignId;
  const lock = useRef(false);
  useEffect(() => {
    generation.current++;
    setKind(initialKind); setConfirmation(null); setSending(false); setWaitingRead(null); setNotice(null); setNoticeCode(null); lock.current = false;
  }, [campaignId, initialKind]);
  useEffect(() => {
    if (waitingRead && connected && worker.lastSuccessReadId !== null && worker.lastSuccessReadId >= waitingRead.minimumReadId) setWaitingRead(null);
  }, [waitingRead, connected, worker.lastSuccessReadId]);
  const value = worker.data?.scope === 'known' ? worker.data.value : null;
  const own = value?.campaignId === campaignId;
  const blocked = !connected || sending || waitingRead !== null;
  const canStart = !blocked && value?.state !== 'stopping' && !(own && value?.state === 'running');
  const canStop = !blocked && own && value?.state === 'running';

  const send = async (action: 'start' | 'stop', selectedKind?: WorkerKind) => {
    if (lock.current || !connected || (action === 'start' ? !canStart : !canStop)) return;
    lock.current = true; setSending(true); setNotice(null); setNoticeCode(null);
    const submittedCampaign = campaignId;
    const submittedGeneration = generation.current;
    let reconcile = false;
    try {
      const result = await post<WorkerState>(campaignPath(submittedCampaign, `/worker/${action}`), {
        campaignId: submittedCampaign, ...(action === 'start' ? { kind: selectedKind } : {}),
      });
      if (!result || !['idle', 'running', 'stopping'].includes(result.state)) throw new ApiError('command_unknown');
      if (identity.current === submittedCampaign && generation.current === submittedGeneration) {
        reconcile = true; setNotice('Comando recebido. Consultando o estado atual; isto não confirma conclusão de Jobs.');
      }
    } catch (error) {
      if (identity.current === submittedCampaign && generation.current === submittedGeneration) {
        const safe = error instanceof ApiError ? error : new ApiError('command_unknown');
        setNotice(safe.message); setNoticeCode(safe.code);
        if (safe.code === 'command_unknown') reconcile = true;
      }
    } finally {
      if (identity.current === submittedCampaign && generation.current === submittedGeneration) {
        const minimumReadId = worker.refresh();
        if (reconcile) setWaitingRead({ minimumReadId });
        setSending(false); setConfirmation(null); lock.current = false; onRefresh();
      }
    }
  };

  return <div className="worker-controls">
    <label>Tipo de processamento <select value={kind} disabled={blocked || confirmation !== null} onChange={event => setKind(event.target.value as WorkerKind)}>
      {kinds.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
    </select></label>
    <div className="actions"><button disabled={!canStart || confirmation !== null} onClick={() => setConfirmation({ campaignId, kind })}>Iniciar processamento pendente</button>
      <button disabled={!canStop || confirmation !== null} onClick={() => { void send('stop'); }}>Parar processamento</button></div>
    <p>Parar impede novas capturas e aguarda o trabalho atual. Não cancela tarefas em andamento nem devolve créditos.</p>
    {!connected && <p role="status">Comandos indisponíveis até recuperar as leituras da API.</p>}
    {confirmation?.campaignId === campaignId && <div role="group" aria-label="Confirmação de início" className="confirmation">
      <p>Iniciar {kinds.find(([id]) => id === confirmation.kind)?.[1]} nesta campanha?</p><p>Processa todas as tarefas elegíveis deste tipo nesta campanha, não apenas a tarefa exibida. Tarefas já enfileiradas podem consumir créditos.</p>
      <button disabled={blocked} onClick={() => { void send('start', confirmation.kind); }}>Confirmar início</button>
      <button disabled={sending} onClick={() => setConfirmation(null)}>Cancelar</button>
    </div>}
    {notice && <p role="status">{notice}</p>}
    {noticeCode && <details><summary>Detalhes técnicos do erro</summary><p>{noticeCode}</p></details>}
    {waitingRead && <button onClick={onRefresh}>Consultar estado novamente</button>}
  </div>;
}
