import type { ProfileView } from './profileApi';

const text = {
  enabled: false, styleId: 'plain', font: null, fontWeight: 700, fontSizePx: 60,
  lineHeight: 1.1, color: '#FFFFFF', strokeWidthPx: 0, strokeColor: '#000000',
  shadowEnabled: false, shadowColor: '#000000', shadowOffsetX: 0, shadowOffsetY: 0,
  backgroundEnabled: false, backgroundColor: '#000000', backgroundPaddingPx: 0,
  anchor: 'top' as const, x: 0.5, y: 0.1, safeTop: 0.05, safeRight: 0.05,
  safeBottom: 0.05, safeLeft: 0.05, maxLines: 3, fitPolicy: 'wrap' as const,
};
export const PROFILE: ProfileView = {
  profileHash: 'a'.repeat(64),
  profile: {
    schemaVersion: '1.0', profileId: 'plain', name: 'Plain', version: 1, createdAt: '2026-10-07T00:00:00Z',
    defaults: {
      output: { width: 1080, height: 1920, fps: 30, format: 'mp4', codec: 'h264' },
      headline: { ...text, startSec: 0, endSec: null },
      captions: { ...text, anchor: 'bottom', y: 0.8, highlightEnabled: false, highlightColor: '#FFFF00' },
      music: { enabled: false, asset: null, volumeDb: -22, duckUnderVoiceDb: -8, loop: true,
        trimStartSec: 0, trimEndSec: null, fadeInSec: 0.4, fadeOutSec: 1 },
      framing: { fit: 'cover', scale: 1, x: 0.5, y: 0.5, zoomStart: 1, zoomEnd: 1 },
    },
  },
};
export function profileFixture(): ProfileView { return structuredClone(PROFILE); }
