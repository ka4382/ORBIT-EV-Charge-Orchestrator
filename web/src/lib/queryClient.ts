import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '../api/client';

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        retry: (count, err) => {
          if (
            err instanceof ApiError &&
            (err.status === 401 || err.status === 403 || err.status === 404 || err.status === 501)
          )
            return false;
          return count < 2;
        },
        refetchOnWindowFocus: false,
      },
    },
  });
}
