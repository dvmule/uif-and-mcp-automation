// RFC 7807 problem details — the single error shape for the whole API.
import type { Response } from 'express';

const BASE = 'https://api.product.example.com/problems';

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  instance?: string;
}

export function problem(
  kind: string,
  title: string,
  status: number,
  detail: string,
  instance: string,
): Problem {
  return { type: `${BASE}/${kind}`, title, status, detail, instance };
}

// Writes a problem+json response with the correct content type and status.
export function sendProblem(res: Response, p: Problem): void {
  res.status(p.status).type('application/problem+json').json(p);
}
