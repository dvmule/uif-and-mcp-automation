import '@testing-library/jest-dom';

import { setConfigForTesting } from '@/services/config';

// The suite used to get its API origin from vitest's env.VITE_API_URL. That
// variable is gone, so the same value is seeded here instead -- one place,
// before any test module imports apiClient.
setConfigForTesting({ apiUrl: 'http://localhost:8000' });
