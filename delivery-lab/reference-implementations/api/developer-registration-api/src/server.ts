// Bootstrap: bind the app to a port. The image runs this.
import { createApp } from './app.js';

const port = Number(process.env.PORT ?? 8080);
createApp().listen(port, () => {
  process.stdout.write(JSON.stringify({ level: 'info', msg: 'listening', port }) + '\n');
});
