import axios from 'axios';

import { getConfig } from '@/services/config';

export const apiClient = axios.create({
  timeout: 15_000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// baseURL is resolved per request rather than at module load, because the API
// origin now comes from /config.json (fetched at boot) instead of a
// VITE_API_URL compiled into the bundle. Setting it here keeps apiClient's
// public shape unchanged, so no feature hook has to know any of this happened.
apiClient.interceptors.request.use((request) => {
  request.baseURL = `${getConfig().apiUrl}/api/v1`;
  return request;
});

// TODO(W1+): auth interceptor — attach Pearson Identity bearer token once IES integration ships
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    // Surface server error messages so feature hooks can show meaningful errors
    if (error.response?.data?.detail) {
      error.message = error.response.data.detail;
    }
    return Promise.reject(error);
  },
);
