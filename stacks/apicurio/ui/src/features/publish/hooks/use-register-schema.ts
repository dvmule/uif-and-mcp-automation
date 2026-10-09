import { useMutation, useQueryClient } from '@tanstack/react-query';
import { refreshNonCompliant } from '@/features/schemas';
import { registerSchema } from '../services/publish.service';
import type { DirectSchemaRegisterRequest, SchemaRegistrationResult } from '../types/publish.types';

interface RegisterArgs {
  groupId: string;
  artifactId: string;
  body: DirectSchemaRegisterRequest;
}

export function useRegisterSchema() {
  const queryClient = useQueryClient();
  return useMutation<SchemaRegistrationResult, Error, RegisterArgs>({
    mutationFn: ({ groupId, artifactId, body }) => registerSchema(groupId, artifactId, body),
    onSuccess: async (result) => {
      // Only act when something actually landed: the endpoint returns 200 with
      // registered:false on failure.
      if (!result.registered) return;

      /**
       * The compliance verdict is cached by the BFF, in process, and
       * `GET /schemas/non-compliant` serves that cache — so invalidating the
       * query re-fetches the verdict from BEFORE this registration. Both
       * surfaces publishing leads to show that verdict: the list's
       * Non-compliant tab, and the badge on the schema page the publish
       * navigates to. Rewriting the metadata block and then being told it is
       * still non-compliant is precisely the wrong answer, and it is the
       * answer someone editing a schema to FIX its compliance would get.
       *
       * `POST /non-compliant/refresh` is the only thing that clears that
       * cache, so it has to be called here rather than left to the list
       * toolbar's button. Awaited, so the page navigated to renders the new
       * verdict rather than the old one and then a correction a moment later —
       * it re-validates every subject, which is a handful of registry calls at
       * this registry's size.
       */
      try {
        await refreshNonCompliant();
      } catch {
        // A failed re-validation leaves the cached verdict stale, which is
        // what it was before this ran. Not worth failing a registration that
        // succeeded over, and the list toolbar's refresh is still the manual
        // way out.
      }

      // Two namespaces, deliberately: `['schemas']` is the list, the tab
      // counts, the overview stats and the compliance results; and
      // `['schema-detail']` is one subject's versions and content, which a new
      // version also changes.
      queryClient.invalidateQueries({ queryKey: ['schemas'] });
      queryClient.invalidateQueries({ queryKey: ['schema-detail'] });
    },
  });
}
