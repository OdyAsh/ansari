import tseslint from 'typescript-eslint';
import ansari from '@ansari/eslint-config/base';

export default [
  ...ansari,
  // Test fixtures remain covered by typecheck and the test runner, as in apps/api.
  {ignores: ['test/**', 'dist-worker/**', '.wrangler/**', 'test-results/**', 'playwright-report/**']},
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {projectService: false, sourceType: 'module'},
    },
  },
];
