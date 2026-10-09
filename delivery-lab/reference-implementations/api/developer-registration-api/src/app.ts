// Express app wiring. createApp() takes its dependencies so tests can inject a
// fresh store. Handlers serve the contract exactly — same paths, same status
// codes, same problem+json errors as openapi.yaml.
import express, { type Request, type Response } from 'express';
import { validateRegisterDeveloper } from './domain.js';
import { DeveloperStore, EmailTakenError } from './store.js';
import { problem, sendProblem } from './problem.js';
import { observability, requireBearer } from './middleware.js';

export function createApp(store: DeveloperStore = new DeveloperStore()) {
  const app = express();
  app.use(express.json());
  app.use(observability);

  const router = express.Router();
  router.use(requireBearer);

  // POST /developers — register a developer.
  router.post('/developers', (req: Request, res: Response) => {
    const errors = validateRegisterDeveloper(req.body);
    if (errors.length > 0) {
      sendProblem(
        res,
        problem('validation-error', 'Validation failed', 400, errors[0]!.message, req.originalUrl),
      );
      return;
    }
    try {
      const developer = store.create(req.body);
      res
        .status(201)
        .location(`/api/v1/developers/${developer.id}`)
        .json(developer);
    } catch (err) {
      if (err instanceof EmailTakenError) {
        sendProblem(
          res,
          problem('email-taken', 'Email already registered', 409, `A developer with email ${err.message} already exists`, req.originalUrl),
        );
        return;
      }
      throw err;
    }
  });

  // GET /developers — list a page.
  router.get('/developers', (req: Request, res: Response) => {
    const pageSize = clampPageSize(req.query.pageSize);
    const cursor = typeof req.query.cursor === 'string' ? req.query.cursor : undefined;
    res.status(200).json(store.list(pageSize, cursor));
  });

  // GET /developers/:developerId — fetch one.
  router.get('/developers/:developerId', (req: Request, res: Response) => {
    const developerId = req.params.developerId ?? '';
    const developer = store.get(developerId);
    if (!developer) {
      sendProblem(
        res,
        problem('not-found', 'Developer not found', 404, `No developer with id ${developerId}`, req.originalUrl),
      );
      return;
    }
    res.status(200).json(developer);
  });

  app.use('/api/v1', router);

  // Uniform 500 — never leak internals, always problem+json.
  app.use((err: unknown, req: Request, res: Response, _next: express.NextFunction) => {
    process.stderr.write(JSON.stringify({ level: 'error', msg: 'unhandled', error: String(err), correlationId: req.correlationId }) + '\n');
    sendProblem(res, problem('internal', 'Internal server error', 500, 'An unexpected error occurred', req.originalUrl));
  });

  return app;
}

function clampPageSize(raw: unknown): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 50;
  return Math.min(100, Math.max(1, Math.trunc(n)));
}
