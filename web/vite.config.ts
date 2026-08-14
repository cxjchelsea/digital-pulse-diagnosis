import {loadEnv} from 'vite';
import {defineConfig} from 'vitest/config';
import react from '@vitejs/plugin-react';

function resolveSoftwareCommitSha(mode: string): string {
  const env = loadEnv(mode, process.cwd(), '');
  const candidates = [
    env.VITE_M1_SOFTWARE_COMMIT_SHA,
    process.env.VITE_M1_SOFTWARE_COMMIT_SHA,
    process.env.GITHUB_SHA,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === 'string') {
      const normalized = candidate.trim().toLowerCase();
      if (/^[0-9a-f]{40}$/.test(normalized) && normalized !== '0'.repeat(40)) {
        return normalized;
      }
    }
  }
  // Vitest 模式提供可验证的非哨兵 SHA，便于对抗测试覆盖持久化重放
  if (mode === 'test') {
    return 'c'.repeat(40);
  }
  // 生产/开发未注入时留空：持久化重放将被前端拒绝，避免伪造全零
  return '';
}

export default defineConfig(({mode}) => {
  const softwareCommitSha = resolveSoftwareCommitSha(mode);
  return {
    plugins: [react()],
    define: {
      'import.meta.env.VITE_M1_SOFTWARE_COMMIT_SHA': JSON.stringify(softwareCommitSha),
    },
    server: {
      proxy: {
        '/api': 'http://127.0.0.1:8000',
        '/ws': {
          target: 'http://127.0.0.1:8000',
          ws: true,
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: true,
    },
  };
});
