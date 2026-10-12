export const campaignStages = [
  ['overview', 'Visão geral'],
  ['script', 'Roteiro'],
  ['images', 'Imagens'],
  ['voice', 'Voz'],
  ['videos', 'Vídeos'],
  ['editing', 'Edição'],
  ['finalization', 'Finalização'],
] as const;

export type CampaignStage = (typeof campaignStages)[number][0];

export function campaignStagePath(campaignId: string, stage: CampaignStage): string {
  return `#/campaigns/${encodeURIComponent(campaignId)}/${stage}`;
}
