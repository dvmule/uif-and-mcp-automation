// Cross-cutting middleware: security (bearer auth) and observability
// (correlation id + structured request log). Both are contract-level concerns,
// not afterthoughts.
import type { NextFunction, Request, Response } from 'express';
import { ulid } from 'ulid';
import { problem, sendProblem } from './problem.js';

declare module 'express-serve-static-core' {
  interface Request {
    correlationId?: string;
  }
}

// Security by default: reject any request without a bearer token. A real
// deployment verifies the JWT against the OIDC issuer; here we only enforce
// presence so the contract's security scheme is exercised.
export function requireBearer(req: Request, res: Response, next: NextFunction): void {
  const auth = req.header('authorization');
  if (!auth || !auth.startsWith('Bearer ')) {
    sendProblem(
      res,
      problem('unauthorized', 'Unauthorized', 401, 'A valid bearer token is required', req.originalUrl),
    );
    return;
  }
  next();
}

// Observability by default: propagate a W3C-style correlation id and emit one
// structured JSON log line per request with the RED signals (rate/errors/duration).
export function observability(req: Request, res: Response, next: NextFunction): void {
  const correlationId = req.header('x-correlation-id') ?? ulid();
  req.correlationId = correlationId;
  res.setHeader('x-correlation-id', correlationId);
  const start = process.hrtime.bigint();
  res.on('finish', () => {
    const durationMs = Number(process.hrtime.bigint() - start) / 1e6;
    process.stdout.write(
      JSON.stringify({
        level: res.statusCode >= 500 ? 'error' : 'info',
        msg: 'http_request',
        method: req.method,
        route: req.route?.path ?? req.path,
        status: res.statusCode,
        durationMs: Math.round(durationMs * 100) / 100,
        correlationId,
      }) + '\n',
    );
  });
  next();
}
