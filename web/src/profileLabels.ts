import type { ProfileView } from './profileApi';

export function profileOptionLabel(view: ProfileView, all: ProfileView[]): string {
  const { profileId, name, version } = view.profile;
  const label = `${name} · v${version}`;
  const peers = all.filter(item => item.profile.name === name && item.profile.version === version);
  if (peers.length < 2) return label;
  const shortId = profileId.slice(-8);
  return `${label} · ${peers.filter(item => item.profile.profileId.endsWith(shortId)).length > 1 ? profileId : shortId}`;
}
